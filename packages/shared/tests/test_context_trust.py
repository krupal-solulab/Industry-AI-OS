"""Trust-boundary tests: the signed context header must be tamper-evident, and
cross-tenant access must be refused before any policy check."""

import pytest

from ai_os_shared.auth import mint_context_header, verify_context_header
from ai_os_shared.authz import check
from ai_os_shared.errors import AuthorizationError, TenantContextError
from ai_os_shared.settings import Settings
from ai_os_shared.tenant_context import TenantContext
from ai_os_shared.types import Principal, Resource, Role

SETTINGS = Settings(INTERNAL_CONTEXT_SECRET="test-secret")


def _ctx(tenant="t1"):
    return TenantContext(
        tenant_id=tenant, user_id="u1", email="u@x.io", roles=[Role.ADMIN]
    )


def test_context_roundtrip():
    ctx = _ctx()
    header = mint_context_header(ctx, SETTINGS)
    out = verify_context_header(header, SETTINGS)
    assert out.tenant_id == ctx.tenant_id
    assert out.roles == ctx.roles


def test_tampered_payload_rejected():
    header = mint_context_header(_ctx(), SETTINGS)
    payload, sig = header.split(".", 1)
    # Flip the payload but keep the old signature.
    forged = payload[:-2] + ("AA" if not payload.endswith("AA") else "BB") + "." + sig
    with pytest.raises(TenantContextError):
        verify_context_header(forged, SETTINGS)


def test_wrong_secret_rejected():
    header = mint_context_header(_ctx(), SETTINGS)
    with pytest.raises(TenantContextError):
        verify_context_header(header, Settings(INTERNAL_CONTEXT_SECRET="other-secret"))


async def test_cross_tenant_denied_before_cerbos():
    """A principal in tenant A may never touch a resource in tenant B — enforced
    without even consulting Cerbos."""
    principal = Principal(id="u1", tenant_id="tenant-A", roles=[Role.OWNER])
    resource = Resource(kind="document", id="d1", tenant_id="tenant-B")
    with pytest.raises(AuthorizationError):
        await check(principal, "read", resource)
