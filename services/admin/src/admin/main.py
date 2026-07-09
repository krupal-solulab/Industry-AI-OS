"""Admin API endpoints."""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.auth import INTERNAL_HEADER
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import admin_session, get_engine
from ai_os_shared.errors import NotFoundError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource

health = HealthRegistry("admin")


async def _db_check() -> str:
    async with admin_session() as s:
        await s.execute(text("SELECT 1"))
    return "ok"


health.register("postgres", _db_check)

app = create_app(service_name="admin", title="AIOS Admin API", health_registry=health)


@app.on_event("startup")
async def _startup() -> None:
    get_engine()


# --------------------------------------------------------------------------- #
# Tenant registry (control plane) — creation is server-to-server (seed/platform).
# --------------------------------------------------------------------------- #
class TenantIn(BaseModel):
    slug: str
    name: str
    keycloak_org_id: str | None = None
    settings: dict = {}


@app.post("/internal/tenants", status_code=201, tags=["internal"])
async def register_tenant(body: TenantIn) -> dict:
    """Upsert a tenant into the control-plane registry. Used by the seed job and
    platform provisioning; not exposed through the gateway."""
    async with admin_session() as s:
        row = (
            await s.execute(
                text(
                    """INSERT INTO tenants (slug, name, keycloak_org_id, settings)
                       VALUES (:slug, :name, :org, CAST(:settings AS jsonb))
                       ON CONFLICT (slug) DO UPDATE
                         SET name = :name, keycloak_org_id = :org,
                             settings = CAST(:settings AS jsonb)
                       RETURNING id, slug, name"""
                ),
                {
                    "slug": body.slug, "name": body.name, "org": body.keycloak_org_id,
                    "settings": json.dumps(body.settings),
                },
            )
        ).one()
    return {"id": str(row.id), "slug": row.slug, "name": row.name}


@app.get("/tenant", tags=["admin"])
async def current_tenant() -> dict:
    ctx = require_context()
    await check_ctx(ctx, "read", Resource(kind="tenant", id=ctx.tenant_id, tenant_id=ctx.tenant_id))
    async with admin_session() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, slug, name, status, settings, created_at FROM tenants "
                    "WHERE id = :id OR keycloak_org_id = :id OR slug = :slug"
                ),
                {"id": ctx.tenant_id, "slug": ctx.tenant_slug or ctx.tenant_id},
            )
        ).first()
    if not row:
        return {
            "id": ctx.tenant_id, "slug": ctx.tenant_slug, "name": ctx.tenant_slug,
            "status": "unregistered",
            "note": "Tenant authenticated via Keycloak but not in the control-plane registry.",
        }
    return {
        "id": str(row.id), "slug": row.slug, "name": row.name, "status": row.status,
        "settings": row.settings, "created_at": row.created_at.isoformat(),
    }


class TenantSettings(BaseModel):
    settings: dict


@app.put("/tenant/settings", tags=["admin"])
async def update_settings(body: TenantSettings) -> dict:
    ctx = require_context()
    await check_ctx(
        ctx, "update_settings", Resource(kind="tenant", id=ctx.tenant_id, tenant_id=ctx.tenant_id)
    )
    async with admin_session() as s:
        await s.execute(
            text(
                "UPDATE tenants SET settings = CAST(:s AS jsonb) "
                "WHERE id = :id OR keycloak_org_id = :id OR slug = :slug"
            ),
            {"s": json.dumps(body.settings), "id": ctx.tenant_id, "slug": ctx.tenant_slug},
        )
    await emit(
        "tenant.update_settings", resource_kind="tenant", resource_id=ctx.tenant_id,
        after=body.settings,
    )
    return {"status": "updated", "settings": body.settings}


# --------------------------------------------------------------------------- #
# Proxies (forward the signed context so downstream RLS + authz still apply).
# --------------------------------------------------------------------------- #
async def _proxy_get(request: Request, base_url: str, path: str) -> dict | list:
    header = request.headers.get(INTERNAL_HEADER)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{base_url.rstrip('/')}{path}",
            headers={INTERNAL_HEADER: header} if header else {},
            params=dict(request.query_params),
        )
        resp.raise_for_status()
        return resp.json()


@app.get("/users", tags=["admin"])
async def users(request: Request):
    return await _proxy_get(request, get_settings().identity_url, "/users")


@app.get("/connectors", tags=["admin"])
async def connectors(request: Request):
    return await _proxy_get(request, get_settings().connectors_url, "/connectors")


@app.get("/audit", tags=["admin"])
async def audit(request: Request):
    return await _proxy_get(request, get_settings().audit_url, "/events")


# --------------------------------------------------------------------------- #
# System health — aggregate readiness across all services.
# --------------------------------------------------------------------------- #
@app.get("/system/health", tags=["admin"])
async def system_health() -> dict:
    ctx = require_context()
    await check_ctx(ctx, "read", Resource(kind="tenant", id=ctx.tenant_id, tenant_id=ctx.tenant_id))
    s = get_settings()
    targets = {
        "identity": s.identity_url, "authz": s.authz_url, "orchestrator": s.orchestrator_url,
        "knowledge": s.knowledge_url, "workflows": s.workflows_url,
        "connectors": s.connectors_url, "audit": s.audit_url,
    }

    async def probe(name: str, url: str) -> tuple[str, str]:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                r = await client.get(f"{url.rstrip('/')}/readyz")
                data = r.json()
                return name, data.get("status", "unknown")
        except Exception:
            return name, "down"

    results = await asyncio.gather(*(probe(n, u) for n, u in targets.items()))
    statuses = dict(results)
    overall = "ok" if all(v == "ok" for v in statuses.values()) else "degraded"
    return {"overall": overall, "services": statuses}


@app.get("/tenants/{slug}", tags=["admin"])
async def get_tenant(slug: str) -> dict:
    ctx = require_context()
    await check_ctx(ctx, "read", Resource(kind="tenant", id=ctx.tenant_id, tenant_id=ctx.tenant_id))
    async with admin_session() as s:
        row = (
            await s.execute(
                text("SELECT id, slug, name, status FROM tenants WHERE slug = :slug"),
                {"slug": slug},
            )
        ).first()
    if not row:
        raise NotFoundError("Tenant not found")
    return {"id": str(row.id), "slug": row.slug, "name": row.name, "status": row.status}
