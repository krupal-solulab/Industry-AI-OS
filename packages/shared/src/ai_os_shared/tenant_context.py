"""Tenant context — the single source of truth for "who is acting, for which tenant".

The context is minted **once**, in the gateway, from a verified Keycloak JWT
(see `auth.py`). It is then propagated to downstream services as a signed header and
stored in a `contextvar` for the lifetime of the request so any code — DB session,
audit emitter, telemetry — can read it without threading it through every call.

No service ever trusts a client-supplied tenant id. The only authority is the
gateway-minted, HMAC-signed context.
"""

from __future__ import annotations

from contextvars import ContextVar

from pydantic import BaseModel, Field

from ai_os_shared.errors import TenantContextError
from ai_os_shared.types import Role


class TenantContext(BaseModel):
    """Immutable per-request identity + tenancy envelope."""

    tenant_id: str
    tenant_slug: str | None = None
    user_id: str
    email: str | None = None
    roles: list[Role] = Field(default_factory=list)
    # Free-form claims carried for ABAC (e.g. department, region). Never a tenant id.
    attributes: dict[str, str] = Field(default_factory=dict)
    # Correlates every log/span/audit row for one request.
    request_id: str | None = None

    model_config = {"frozen": True}

    def has_role(self, *roles: Role) -> bool:
        return any(r in self.roles for r in roles)


# One contextvar per process; async-task-safe.
_ctx: ContextVar[TenantContext | None] = ContextVar("aios_tenant_context", default=None)


def set_context(ctx: TenantContext):
    """Bind the context for the current async task. Returns the reset token."""
    return _ctx.set(ctx)


def reset_context(token) -> None:
    _ctx.reset(token)


def current_context() -> TenantContext | None:
    return _ctx.get()


def require_context() -> TenantContext:
    """Fetch the context or fail loudly. Use in any tenant-scoped code path."""
    ctx = _ctx.get()
    if ctx is None:
        raise TenantContextError("No tenant context bound to this request")
    return ctx
