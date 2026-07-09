"""Authorization façade endpoints."""

from __future__ import annotations

import httpx
from pydantic import BaseModel
from sqlalchemy import text  # noqa: F401  (kept for parity; not used directly)

from ai_os_shared.app import create_app
from ai_os_shared.authz import CerbosClient
from ai_os_shared.health import HealthRegistry
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Principal, Resource, Role

health = HealthRegistry("authz")


async def _cerbos_check() -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=3) as client:
        resp = await client.get(f"{settings.cerbos_url.rstrip('/')}/_cerbos/health")
        resp.raise_for_status()
    return "ok"


health.register("cerbos", _cerbos_check)

app = create_app(service_name="authz", title="AIOS Authorization Service", health_registry=health)
_client = CerbosClient()


class CheckRequest(BaseModel):
    action: str
    resource_kind: str
    resource_id: str = "*"
    resource_attributes: dict[str, str] = {}


class CheckResponse(BaseModel):
    allowed: bool
    action: str
    resource_kind: str


@app.post("/check", response_model=CheckResponse, tags=["authz"])
async def check_endpoint(req: CheckRequest) -> CheckResponse:
    """Evaluate a decision for the CURRENT principal (from the signed context).

    Cross-tenant requests are impossible here: the resource tenant is forced to the
    principal's tenant, so the façade can never be used to probe another tenant.
    """
    ctx = require_context()
    principal = Principal(
        id=ctx.user_id, tenant_id=ctx.tenant_id, roles=ctx.roles, email=ctx.email,
        attributes=ctx.attributes,
    )
    resource = Resource(
        kind=req.resource_kind,
        id=req.resource_id,
        tenant_id=ctx.tenant_id,
        attributes=req.resource_attributes,
    )
    allowed = await _client.is_allowed(principal, req.action, resource)
    return CheckResponse(allowed=allowed, action=req.action, resource_kind=req.resource_kind)


@app.get("/roles", tags=["authz"])
async def roles() -> dict:
    """The starter platform role model. Industry packs may extend this later."""
    return {
        "roles": [r.value for r in Role],
        "hierarchy": {
            "owner": "Full control of the tenant (settings, billing, delete)",
            "admin": "Manage users, connectors, workflows; approve/reject",
            "member": "Use platform capabilities (chat, upload, start workflows)",
            "viewer": "Read-only access",
        },
    }
