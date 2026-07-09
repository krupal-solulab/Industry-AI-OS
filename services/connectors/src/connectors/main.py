"""Connector Hub endpoints: registry, tool discovery, invoke, enable/configure."""

from __future__ import annotations

import json

from pydantic import BaseModel
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import get_engine, tenant_session
from ai_os_shared.errors import NotFoundError, ValidationError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource
from connectors.registry import all_connectors, get_connector

health = HealthRegistry("connectors")
app = create_app(service_name="connectors", title="AIOS Connector Hub", health_registry=health)


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


@app.get("/connectors", tags=["connectors"])
async def list_connectors() -> list[dict]:
    ctx = require_context()
    await check_ctx(ctx, "list", Resource(kind="connector", id="*", tenant_id=ctx.tenant_id))
    state = await _tenant_state(ctx)
    return [
        {
            "key": c.key,
            "name": c.name,
            "kind": c.kind,
            "enabled": state.get(c.key, {}).get("enabled", False),
            "tool_count": len(c.tools),
        }
        for c in all_connectors()
    ]


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
