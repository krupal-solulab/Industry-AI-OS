"""Authorization — the single `check(principal, action, resource)` interface.

Every mutating AND reading endpoint calls this before acting. The implementation
delegates to Cerbos (the policy decision point); policies live as versioned files
in `deploy/cerbos/policies`, so the application stays policy-free. Swapping Cerbos
for OPA later means reimplementing this module only.
"""

from __future__ import annotations

import httpx

from ai_os_shared.errors import AuthorizationError, UpstreamError
from ai_os_shared.settings import Settings, get_settings
from ai_os_shared.tenant_context import TenantContext
from ai_os_shared.types import Principal, Resource, Role


def principal_from_context(ctx: TenantContext) -> Principal:
    return Principal(
        id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        roles=ctx.roles,
        email=ctx.email,
        attributes=ctx.attributes,
    )


class CerbosClient:
    """Thin async client over the Cerbos check API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base = self._settings.cerbos_url.rstrip("/")

    async def is_allowed(
        self, principal: Principal, action: str, resource: Resource
    ) -> bool:
        payload = {
            "requestId": f"{principal.tenant_id}:{principal.id}",
            "principal": {
                "id": principal.id,
                "roles": [r.value for r in principal.roles] or [Role.VIEWER.value],
                "attr": {"tenant_id": principal.tenant_id, **principal.attributes},
            },
            "resources": [
                {
                    "actions": [action],
                    "resource": {
                        "kind": resource.kind,
                        "id": resource.id,
                        "attr": {"tenant_id": resource.tenant_id, **resource.attributes},
                    },
                }
            ],
        }
        url = f"{self._base}/api/check/resources"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Cerbos unreachable: {exc}") from exc

        results = data.get("results", [])
        if not results:
            return False
        return results[0].get("actions", {}).get(action) == "EFFECT_ALLOW"


_client: CerbosClient | None = None


def _get_client() -> CerbosClient:
    global _client
    if _client is None:
        _client = CerbosClient()
    return _client


async def check(principal: Principal, action: str, resource: Resource) -> None:
    """Authorize an action or raise AuthorizationError. The platform's guard rail.

    Cross-tenant access is denied before Cerbos is even consulted: a principal may
    only act on resources in its own tenant.
    """
    if principal.tenant_id != resource.tenant_id:
        raise AuthorizationError(
            "Cross-tenant access denied",
            detail={"principal_tenant": principal.tenant_id, "resource_tenant": resource.tenant_id},
        )
    allowed = await _get_client().is_allowed(principal, action, resource)
    if not allowed:
        raise AuthorizationError(
            f"Not permitted: {action} on {resource.kind}",
            detail={"action": action, "kind": resource.kind, "resource_id": resource.id},
        )


async def check_ctx(ctx: TenantContext, action: str, resource: Resource) -> None:
    """Convenience wrapper: authorize using the bound TenantContext."""
    await check(principal_from_context(ctx), action, resource)
