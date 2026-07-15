"""Connector Hub endpoints: registry, tool discovery, invoke, enable/configure."""

from __future__ import annotations

import json

import httpx
from pydantic import BaseModel
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import get_engine, tenant_session
from ai_os_shared.errors import (
    AuthorizationError,
    NotFoundError,
    UpstreamError,
    ValidationError,
)
from ai_os_shared.health import HealthRegistry
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource, Role
from connectors.registry import all_connectors, get_connector

health = HealthRegistry("connectors")
app = create_app(service_name="connectors", title="AIOS Connector Hub", health_registry=health)


def _require_admin(ctx) -> None:
    """Entitlement management is a tenant-admin action: only owners/admins may grant or
    revoke a connector for the tenant. Members/viewers can still read the list. This is an
    in-code role gate (no Cerbos policy change) on top of the resource-level check_ctx."""
    if not ctx.has_role(Role.OWNER, Role.ADMIN):
        raise AuthorizationError("Only owners or admins can manage connector entitlements")


@app.on_event("startup")
async def _startup() -> None:
    get_engine()


async def _tenant_state(ctx) -> dict[str, dict]:
    """Map connector key -> {enabled, config} from the tenant's stored rows."""
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text("SELECT key, enabled, config FROM connectors WHERE tenant_id = :tid"),
            {"tid": ctx.tenant_id},
        )
        return {r.key: {"enabled": r.enabled, "config": r.config or {}} for r in rows}


async def _entitlement_view(ctx) -> tuple[bool, set[str]]:
    """Returns ``(restricted, allowed_keys)`` for this tenant.

    Model: a tenant is UNRESTRICTED until an admin grants it any connector. With zero
    entitlement rows the whole catalog is available (so the Connector Hub is never empty
    out of the box). The moment a row exists the tenant becomes *restricted* — an allowlist
    — and only `allowed = true` connectors are usable/visible. This gives per-tenant
    curation ("give this client only these two") without the empty-Hub surprise.
    """
    async with tenant_session(ctx) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT connector_key, allowed FROM connector_entitlements "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": ctx.tenant_id},
            )
        ).all()
    restricted = len(rows) > 0
    allowed = {r.connector_key for r in rows if r.allowed}
    return restricted, allowed


def _is_entitled(kind: str, key: str, restricted: bool, allowed: set[str]) -> bool:
    """Reference connectors are always usable; otherwise entitled if the tenant is
    unrestricted, or the key is on its allowlist."""
    return kind == "reference" or not restricted or key in allowed


@app.get("/connectors", tags=["connectors"])
async def list_connectors(all: bool = False) -> list[dict]:
    """List connectors for the tenant.

    By default returns the connectors the tenant may use — the full catalog for an
    unrestricted tenant, or just its allowlist once an admin has restricted it. Pass
    `?all=true` for the full catalog regardless, each item flagged with its `entitled`
    state (the admin/builder palette).
    """
    ctx = require_context()
    await check_ctx(ctx, "list", Resource(kind="connector", id="*", tenant_id=ctx.tenant_id))
    state = await _tenant_state(ctx)
    restricted, allowed = await _entitlement_view(ctx)
    items = [
        {
            "key": c.key,
            "name": c.name,
            "kind": c.kind,
            "enabled": state.get(c.key, {}).get("enabled", False),
            "tool_count": len(c.tools),
            "entitled": _is_entitled(c.kind, c.key, restricted, allowed),
        }
        for c in all_connectors()
    ]
    if not all:
        items = [i for i in items if i["entitled"]]
    return items


@app.get("/connectors/{key}/tools", tags=["connectors"])
async def list_tools(key: str) -> dict:
    ctx = require_context()
    await check_ctx(ctx, "read", Resource(kind="connector", id=key, tenant_id=ctx.tenant_id))
    connector = get_connector(key)
    if not connector:
        raise NotFoundError(f"Unknown connector: {key}")
    return {
        "key": connector.key,
        "tools": [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in connector.tools
        ],
    }


class InvokeBody(BaseModel):
    tool: str
    arguments: dict = {}


@app.post("/connectors/{key}/invoke", tags=["connectors"])
async def invoke(key: str, body: InvokeBody) -> dict:
    ctx = require_context()
    await check_ctx(ctx, "invoke", Resource(kind="connector", id=key, tenant_id=ctx.tenant_id))
    connector = get_connector(key)
    if not connector:
        raise NotFoundError(f"Unknown connector: {key}")

    # Entitlement is checked before the enabled flag: a restricted tenant can only use a
    # connector on its allowlist (an unrestricted tenant may use any; reference always).
    restricted, allowed = await _entitlement_view(ctx)
    if not _is_entitled(connector.kind, key, restricted, allowed):
        raise ValidationError(f"Connector '{key}' is not entitled for this tenant")

    state = await _tenant_state(ctx)
    entry = state.get(key)
    # The echo reference connector is always usable; others must be enabled.
    if connector.kind != "reference" and not (entry and entry["enabled"]):
        raise ValidationError(f"Connector '{key}' is not enabled for this tenant")

    config = (entry or {}).get("config", {})
    try:
        result = await connector.invoke(body.tool, body.arguments, config)
    except (ValueError, NotImplementedError) as exc:
        raise ValidationError(str(exc)) from exc

    await emit(
        "connector.invoke",
        resource_kind="connector",
        resource_id=key,
        metadata={"tool": body.tool},
    )
    return {"connector": key, "tool": body.tool, "result": result}


class ConfigureBody(BaseModel):
    enabled: bool = True
    config: dict = {}


@app.put("/connectors/{key}", tags=["connectors"])
async def configure(key: str, body: ConfigureBody) -> dict:
    """Enable/disable + set per-tenant config (creds, MCP endpoint) for a connector."""
    ctx = require_context()
    await check_ctx(ctx, "configure", Resource(kind="connector", id=key, tenant_id=ctx.tenant_id))
    connector = get_connector(key)
    if not connector:
        raise NotFoundError(f"Unknown connector: {key}")
    # Can't enable a connector a restricted tenant isn't entitled to (reference always ok,
    # unrestricted tenants may enable any). Disabling is always allowed.
    if body.enabled:
        restricted, allowed = await _entitlement_view(ctx)
        if not _is_entitled(connector.kind, key, restricted, allowed):
            raise ValidationError(f"Connector '{key}' is not entitled for this tenant")
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO connectors (tenant_id, key, name, kind, enabled, config)
                   VALUES (:tid, :key, :name, :kind, :enabled, CAST(:config AS jsonb))
                   ON CONFLICT (tenant_id, key)
                   DO UPDATE SET enabled = :enabled, config = CAST(:config AS jsonb)"""
            ),
            {
                "tid": ctx.tenant_id, "key": key, "name": connector.name,
                "kind": connector.kind, "enabled": body.enabled,
                "config": json.dumps(body.config),
            },
        )
    await emit(
        "connector.configure",
        resource_kind="connector",
        resource_id=key,
        after={"enabled": body.enabled},
    )
    return {"key": key, "enabled": body.enabled}


@app.get("/connectors/entitlements", tags=["connectors"])
async def list_entitlements() -> list[dict]:
    """The tenant's connector allowlist rows (the opt-in entitlement grants)."""
    ctx = require_context()
    await check_ctx(ctx, "list", Resource(kind="connector", id="*", tenant_id=ctx.tenant_id))
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text(
                """SELECT connector_key, allowed, created_by, created_at
                   FROM connector_entitlements WHERE tenant_id = :tid
                   ORDER BY connector_key"""
            ),
            {"tid": ctx.tenant_id},
        )
        return [
            {
                "connector_key": r.connector_key,
                "allowed": r.allowed,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


class EntitlementBody(BaseModel):
    allowed: bool = True


@app.put("/connectors/entitlements/{key}", tags=["connectors"])
async def set_entitlement(key: str, body: EntitlementBody) -> dict:
    """Grant (or revoke) a single connector for this tenant. Owner/admin only."""
    ctx = require_context()
    _require_admin(ctx)
    await check_ctx(ctx, "configure", Resource(kind="connector", id=key, tenant_id=ctx.tenant_id))
    if not get_connector(key):
        raise NotFoundError(f"Unknown connector: {key}")
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO connector_entitlements
                       (tenant_id, connector_key, allowed, created_by)
                   VALUES (:tid, :key, :allowed, :by)
                   ON CONFLICT (tenant_id, connector_key)
                   DO UPDATE SET allowed = :allowed, created_by = :by, updated_at = now()"""
            ),
            {"tid": ctx.tenant_id, "key": key, "allowed": body.allowed, "by": ctx.user_id},
        )
    await emit(
        "connector.entitlement",
        resource_kind="connector",
        resource_id=key,
        after={"allowed": body.allowed},
    )
    return {"connector_key": key, "allowed": body.allowed}


@app.post("/connectors/entitlements/grant-defaults", tags=["connectors"])
async def grant_default_entitlements() -> dict:
    """One-time grandfather/bootstrap: entitle the tenant to every non-reference
    connector in the registry. Idempotent — re-running is a no-op for existing grants.
    Owner/admin only."""
    ctx = require_context()
    _require_admin(ctx)
    await check_ctx(ctx, "configure", Resource(kind="connector", id="*", tenant_id=ctx.tenant_id))
    keys = [c.key for c in all_connectors() if c.kind != "reference"]
    async with tenant_session(ctx) as s:
        for key in keys:
            await s.execute(
                text(
                    """INSERT INTO connector_entitlements
                           (tenant_id, connector_key, allowed, created_by)
                       VALUES (:tid, :key, true, :by)
                       ON CONFLICT (tenant_id, connector_key)
                       DO UPDATE SET allowed = true, created_by = :by, updated_at = now()"""
                ),
                {"tid": ctx.tenant_id, "key": key, "by": ctx.user_id},
            )
    await emit(
        "connector.entitlement.grant_defaults",
        resource_kind="connector",
        resource_id="*",
        after={"granted": keys},
    )
    return {"granted": keys, "count": len(keys)}


@app.post("/connectors/{key}/connect-session", tags=["connectors"])
async def connect_session(key: str) -> dict:
    """Create a Nango **Connect session** so THIS tenant's user authorizes the provider
    from inside our app — never Nango's dashboard, never shared credentials. The FE opens
    Nango's Connect UI with the returned `session_token`; on success it PUTs the resulting
    `connection_id` back via `PUT /connectors/{key}` (`config.connection_id`), flipping the
    connector from sandbox to live for this tenant. If NANGO_SECRET_KEY is unset, the
    connector stays in sandbox and no live connect is needed."""
    ctx = require_context()
    await check_ctx(ctx, "configure", Resource(kind="connector", id=key, tenant_id=ctx.tenant_id))
    connector = get_connector(key)
    if not connector:
        raise NotFoundError(f"Unknown connector: {key}")
    if connector.kind != "nango":
        raise ValidationError(f"Connector '{key}' does not use Nango Connect")
    settings = get_settings()
    if not settings.nango_secret_key:
        return {
            "status": "sandbox",
            "message": "No NANGO_SECRET_KEY set — this connector runs in sandbox; no live "
            "connect is required. Set the key + connect to go live.",
        }
    provider = getattr(connector, "provider", key.split(".")[-1])
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.nango_host.rstrip('/')}/connect/sessions",
                headers={"Authorization": f"Bearer {settings.nango_secret_key}"},
                json={
                    "end_user": {"id": ctx.tenant_id, "email": ctx.email or None},
                    "allowed_integrations": [provider],
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise UpstreamError(f"Nango connect-session failed: {exc}") from exc
    token = (data.get("data") or {}).get("token") or data.get("token")
    return {"status": "ok", "session_token": token, "provider": provider}
