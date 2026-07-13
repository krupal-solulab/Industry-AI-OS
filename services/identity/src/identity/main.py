"""Identity service endpoints (tenant-scoped)."""

from __future__ import annotations

import httpx
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import admin_session, new_uuid, tenant_session
from ai_os_shared.errors import NotFoundError, ValidationError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.industry import get_industry, industry_keys, list_industries
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import TenantContext, require_context
from ai_os_shared.types import Resource, Role
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
    # ctx.user_id is whatever the access token's sub/preferred_username/email
    # resolves to (this realm's access tokens carry no `sub`, so in practice it's
    # the email) — match on both so this keeps working if that JWT config changes.
    profile = None
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT role, login_source FROM user_profiles "
                    "WHERE keycloak_user_id = :uid OR email = :email"
                ),
                {"uid": ctx.user_id, "email": ctx.email or ""},
            )
        ).mappings().first()
        if row:
            profile = dict(row)
    return {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "tenant_id": ctx.tenant_id,
        "tenant_slug": ctx.tenant_slug,
        "roles": [r.value for r in ctx.roles],
        "role": profile["role"] if profile else None,
        "login_source": profile["login_source"] if profile else None,
    }


@app.get("/industries", tags=["identity"])
async def industries() -> list[dict]:
    """The configured industries (one per non-generic pack). Drives signup dropdowns.
    Also served publicly by the gateway pre-login; here it's the authed equivalent."""
    return [i.summary() for i in list_industries()]


@app.get("/workspace/config", tags=["identity"])
async def workspace_config() -> dict:
    """The workspace configuration for the CURRENT user's industry — nav, theme,
    terminology, entities, available copilots/packs. The industry-specific frontend
    calls this right after login to render the correct workspace against the shared API.

    Falls back to a bare config (no industry) for users with no `login_source` (e.g. the
    original demo users that predate self-service signup)."""
    ctx = require_context()
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT login_source FROM user_profiles "
                    "WHERE keycloak_user_id = :uid OR email = :email"
                ),
                {"uid": ctx.user_id, "email": ctx.email or ""},
            )
        ).mappings().first()
    login_source = row["login_source"] if row else None
    industry = get_industry(login_source)
    return {
        "login_source": login_source,
        "industry": industry.key if industry else None,
        "workspace": industry.workspace.model_dump() if industry else None,
        "workflow_packs": industry.workflow_packs if industry else [],
    }


DEFAULT_SIGNUP_ROLE = Role.MEMBER
SIGNUP_TENANT_SLUG = "demo"  # self-service signup joins the shared demo tenant


class RegisterUser(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    password: str
    login_source: str


@app.post("/internal/register", response_model=UserOut, status_code=201, tags=["auth"])
async def register(body: RegisterUser) -> UserOut:
    """Public self-service signup. Not exposed directly — only the gateway's
    POST /auth/register calls this, service-to-service, before any user token exists.

    Keycloak remains the credential store (ADR-0001): this creates the real Keycloak
    user + issues it the platform's default role, then records the platform-specific
    profile (role, login_source) in our own DB. New signups always land as `member` of
    the shared demo tenant — never owner/admin — to avoid a public signup form handing
    out elevated privileges on a shared tenant.
    """
    valid_sources = industry_keys()
    if body.login_source not in valid_sources:
        raise ValidationError(f"login_source must be one of {sorted(valid_sources)}")

    async with admin_session() as s:
        row = (
            await s.execute(
                text("SELECT keycloak_org_id FROM tenants WHERE slug = :slug"),
                {"slug": SIGNUP_TENANT_SLUG},
            )
        ).mappings().first()
    if not row or not row["keycloak_org_id"]:
        raise NotFoundError("Signup tenant is not provisioned")
    org_id = row["keycloak_org_id"]

    user_id = await _kc.create_user(
        org_id, str(body.email), body.first_name, body.last_name, body.password
    )
    await _kc.assign_realm_role(user_id, DEFAULT_SIGNUP_ROLE.value)

    ctx = TenantContext(
        tenant_id=SIGNUP_TENANT_SLUG,
        tenant_slug=SIGNUP_TENANT_SLUG,
        user_id=user_id,
        email=str(body.email),
        roles=[DEFAULT_SIGNUP_ROLE],
    )
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO user_profiles
                   (id, tenant_id, keycloak_user_id, email, first_name, last_name,
                    role, login_source)
                   VALUES (:id, :tid, :kcid, :email, :first, :last, :role, :src)"""
            ),
            {
                "id": new_uuid(), "tid": SIGNUP_TENANT_SLUG, "kcid": user_id,
                "email": str(body.email), "first": body.first_name, "last": body.last_name,
                "role": DEFAULT_SIGNUP_ROLE.value, "src": body.login_source,
            },
        )
    await emit(
        "user.register",
        resource_kind="user",
        resource_id=user_id,
        after={"email": str(body.email), "login_source": body.login_source},
    )
    return UserOut(
        id=user_id,
        email=str(body.email),
        first_name=body.first_name,
        last_name=body.last_name,
        roles=[DEFAULT_SIGNUP_ROLE.value],
    )


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
