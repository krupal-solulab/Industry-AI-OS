"""Identity service endpoints (tenant-scoped)."""

from __future__ import annotations

import httpx
from pydantic import BaseModel, EmailStr

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.authz import check_ctx
from ai_os_shared.health import HealthRegistry
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource
from identity.keycloak_admin import KeycloakAdmin

health = HealthRegistry("identity")


async def _kc_check() -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=3) as client:
        resp = await client.get(
            f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
            "/.well-known/openid-configuration"
        )
        resp.raise_for_status()
    return "ok"


health.register("keycloak", _kc_check)

app = create_app(service_name="identity", title="AIOS Identity Service", health_registry=health)
_kc = KeycloakAdmin()


class UserOut(BaseModel):
    id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    roles: list[str] = []


class CreateUser(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    password: str
    role: str = "member"


@app.get("/me", tags=["identity"])
async def me() -> dict:
    """The current principal, as resolved from the verified JWT by the gateway."""
    ctx = require_context()
    return {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "tenant_id": ctx.tenant_id,
        "tenant_slug": ctx.tenant_slug,
        "roles": [r.value for r in ctx.roles],
    }


@app.get("/users", response_model=list[UserOut], tags=["identity"])
async def list_users() -> list[UserOut]:
    ctx = require_context()
    await check_ctx(
        ctx, "manage_users", Resource(kind="tenant", id=ctx.tenant_id, tenant_id=ctx.tenant_id)
    )
    members = await _kc.list_org_members(ctx.tenant_id)
    out: list[UserOut] = []
    for m in members:
        roles = await _kc.get_user_roles(m["id"])
        out.append(
            UserOut(
                id=m["id"],
                email=m.get("email"),
                first_name=m.get("firstName"),
                last_name=m.get("lastName"),
                roles=roles,
            )
        )
    return out


@app.post("/users", response_model=UserOut, status_code=201, tags=["identity"])
async def create_user(body: CreateUser) -> UserOut:
    ctx = require_context()
    await check_ctx(
        ctx, "manage_users", Resource(kind="tenant", id=ctx.tenant_id, tenant_id=ctx.tenant_id)
    )
    user_id = await _kc.create_user(
        ctx.tenant_id, str(body.email), body.first_name, body.last_name, body.password
    )
    await _kc.assign_realm_role(user_id, body.role)
    await emit(
        "user.create",
        resource_kind="user",
        resource_id=user_id,
        after={"email": str(body.email), "role": body.role},
    )
    return UserOut(
        id=user_id,
        email=str(body.email),
        first_name=body.first_name,
        last_name=body.last_name,
        roles=[body.role],
    )


class AssignRole(BaseModel):
    role: str


@app.post("/users/{user_id}/roles", tags=["identity"])
async def assign_role(user_id: str, body: AssignRole) -> dict:
    ctx = require_context()
    await check_ctx(
        ctx, "assign_roles", Resource(kind="tenant", id=ctx.tenant_id, tenant_id=ctx.tenant_id)
    )
    await _kc.assign_realm_role(user_id, body.role)
    await emit(
        "user.assign_role",
        resource_kind="user",
        resource_id=user_id,
        after={"role": body.role},
    )
    return {"user_id": user_id, "role": body.role, "status": "assigned"}
