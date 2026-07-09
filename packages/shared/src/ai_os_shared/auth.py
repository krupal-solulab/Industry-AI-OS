"""Authentication + internal trust boundary.

Two responsibilities, both isolating the rest of the platform from crypto details:

1. **JWT validation** (gateway only): verify a Keycloak-issued access token against
   the realm JWKS, and derive a `TenantContext` from its claims — importantly the
   Organization membership, which maps to the tenant.

2. **Internal context signing**: the gateway mints an HMAC-signed header carrying the
   context; downstream services verify it. This is what lets a service trust the
   tenant id it receives without re-validating the user's JWT on every hop, while
   still rejecting anything a client tries to forge.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
from jose import jwt
from jose.exceptions import JWTError

from ai_os_shared.errors import AuthenticationError, TenantContextError
from ai_os_shared.settings import Settings, get_settings
from ai_os_shared.tenant_context import TenantContext
from ai_os_shared.types import Role

INTERNAL_HEADER = "X-AIOS-Context"
REQUEST_ID_HEADER = "X-AIOS-Request-Id"

_jwks_cache: dict[str, tuple[float, dict]] = {}
_JWKS_TTL = 3600.0


async def _fetch_jwks(settings: Settings) -> dict:
    """Fetch (and cache) the realm's JWKS for signature verification.

    JWKS is fetched over the INTERNAL network URL (`keycloak_url`, reachable from
    inside Docker/K8s), while the token's `iss` claim is validated against the
    PUBLIC `keycloak_issuer`. Decoupling the two avoids the classic issuer-mismatch
    trap where tokens minted via the public URL fail validation inside the cluster.
    """
    key = settings.keycloak_url
    now = time.time()
    cached = _jwks_cache.get(key)
    if cached and now - cached[0] < _JWKS_TTL:
        return cached[1]
    url = (
        f"{settings.keycloak_url.rstrip('/')}/realms/"
        f"{settings.keycloak_realm}/protocol/openid-connect/certs"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        jwks = resp.json()
    _jwks_cache[key] = (now, jwks)
    return jwks


def _roles_from_claims(claims: dict, client_id: str) -> list[Role]:
    """Extract platform roles from Keycloak realm/client role claims."""
    raw: list[str] = []
    raw += claims.get("realm_access", {}).get("roles", [])
    raw += claims.get("resource_access", {}).get(client_id, {}).get("roles", [])
    out: list[Role] = []
    for r in raw:
        try:
            out.append(Role(r.lower()))
        except ValueError:
            continue  # non-platform roles are ignored here
    return out


def _tenant_from_claims(claims: dict) -> tuple[str, str | None]:
    """Resolve tenant id from the Keycloak Organizations claim.

    Keycloak Organizations surface as an `organization` claim. We take the first
    org as the active tenant. A custom `tenant_id` attribute wins if present.
    """
    if tid := claims.get("tenant_id"):
        return str(tid), claims.get("tenant_slug")
    orgs = claims.get("organization")
    if isinstance(orgs, dict) and orgs:
        slug = next(iter(orgs.keys()))
        org = orgs[slug] or {}
        return str(org.get("id", slug)), slug
    if isinstance(orgs, list) and orgs:
        first = orgs[0]
        if isinstance(first, dict):
            return str(first.get("id") or first.get("name")), first.get("name")
        return str(first), str(first)
    raise TenantContextError("JWT carries no organization/tenant claim")


async def context_from_jwt(token: str, settings: Settings | None = None) -> TenantContext:
    """Validate a Keycloak access token and build the TenantContext. Gateway use only."""
    settings = settings or get_settings()
    try:
        jwks = await _fetch_jwks(settings)
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer,
            options={"verify_aud": False},  # multiple audiences across services
        )
    except (JWTError, httpx.HTTPError) as exc:
        raise AuthenticationError(f"Invalid or unverifiable token: {exc}") from exc

    tenant_id, tenant_slug = _tenant_from_claims(claims)
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=str(claims.get("sub") or claims.get("preferred_username") or claims.get("email") or ""),
        email=claims.get("email"),
        roles=_roles_from_claims(claims, settings.keycloak_client_id),
        attributes={
            k: str(v)
            for k, v in claims.items()
            if k in {"department", "region", "preferred_username"}
        },
    )


# --------------------------------------------------------------------------- #
# Internal signed-context header (gateway -> downstream services)
# --------------------------------------------------------------------------- #
def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def mint_context_header(ctx: TenantContext, settings: Settings | None = None) -> str:
    """Serialize + HMAC-sign the context into a compact `payload.signature` token."""
    settings = settings or get_settings()
    body = ctx.model_dump(mode="json")
    payload = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True).encode()).decode()
    sig = _sign(payload.encode(), settings.internal_context_secret)
    return f"{payload}.{sig}"


def verify_context_header(header: str, settings: Settings | None = None) -> TenantContext:
    """Verify + parse the signed context. Raises if forged/tampered. Downstream use."""
    settings = settings or get_settings()
    try:
        payload, sig = header.split(".", 1)
    except ValueError as exc:
        raise TenantContextError("Malformed internal context header") from exc
    expected = _sign(payload.encode(), settings.internal_context_secret)
    if not hmac.compare_digest(sig, expected):
        raise TenantContextError("Internal context signature mismatch — refusing request")
    body = json.loads(base64.urlsafe_b64decode(payload.encode()))
    return TenantContext.model_validate(body)
