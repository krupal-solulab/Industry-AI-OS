"""API Gateway application."""

from __future__ import annotations

import uuid

import httpx
import structlog
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from strawberry.fastapi import GraphQLRouter

from ai_os_shared.app import create_app
from ai_os_shared.auth import REQUEST_ID_HEADER, context_from_jwt
from ai_os_shared.errors import PlatformError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import current_context, reset_context, set_context
from gateway.graphql_schema import schema
from gateway.ratelimit import allow
from gateway.routing import SERVICE_KEYS, proxy

log = structlog.get_logger("aios.gateway")
health = HealthRegistry("gateway")

# The gateway MINTS context from a JWT; it does not trust an inbound context header.
app = create_app(
    service_name="gateway",
    title="AIOS API Gateway",
    trust_gateway_context=False,
    health_registry=health,
)

_PUBLIC_PATHS = (
    "/healthz", "/readyz", "/docs", "/openapi.json", "/redoc", "/auth/token", "/auth/register",
)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    path = request.url.path
    # Public paths + the GraphQL playground (GET) skip auth.
    if path.startswith(_PUBLIC_PATHS) or (path == "/graphql" and request.method == "GET"):
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"code": "authentication_error", "message": "Missing bearer token"},
        )
    try:
        ctx = await context_from_jwt(auth.removeprefix("Bearer ").strip())
    except PlatformError as exc:
        # Covers auth failures (401) AND a valid token with no tenant/organization
        # (400) — return a clear message instead of a 500 the browser can't read.
        return JSONResponse(
            status_code=exc.status_code, content={"code": exc.code, "message": exc.message}
        )

    rid = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    ctx = ctx.model_copy(update={"request_id": rid})

    ok, remaining = await allow(ctx.tenant_id, ctx.user_id)
    if not ok:
        return JSONResponse(
            status_code=429,
            content={"code": "rate_limited", "message": "Rate limit exceeded"},
            headers={"Retry-After": "60"},
        )

    token = set_context(ctx)
    request.state.tenant_context = ctx
    try:
        response = await call_next(request)
    finally:
        reset_context(token)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers[REQUEST_ID_HEADER] = rid
    return response


# CORS is added LAST so it is the OUTERMOST middleware — that way even early error
# responses from the auth middleware carry CORS headers and the browser can read them.
#
# Origin policy is environment-driven:
#   - production  -> only the explicit allow-list in CORS_ORIGINS
#   - dev/staging -> reflect ANY origin (open) for frictionless frontend work
# We use allow_origin_regex=".*" for the open case rather than allow_origins=["*"],
# because "*" is INVALID together with allow_credentials=True (browsers reject it);
# the regex reflects the caller's Origin back, which is credential-safe.
_settings = get_settings()
if _settings.is_production:
    _cors_kwargs: dict = {"allow_origins": _settings.cors_origin_list}
else:
    _cors_kwargs = {"allow_origin_regex": ".*"}

app.add_middleware(
    CORSMiddleware,
    **_cors_kwargs,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-AIOS-Request-Id", "X-RateLimit-Remaining"],
)


# ---- GraphQL ----------------------------------------------------------------
async def _graphql_context(request: Request) -> dict:
    # The auth middleware has already bound the context for this request.
    return {"tenant_context": current_context(), "request": request}


graphql_app = GraphQLRouter(schema, context_getter=_graphql_context)
app.include_router(graphql_app, prefix="/graphql")


class TokenRequest(BaseModel):
    username: str
    password: str


# ---- dev helper: exchange username/password for a Keycloak token ------------
@app.post("/auth/token", tags=["auth"])
async def dev_token(body: TokenRequest) -> dict:
    """Convenience login for local dev / the landing-page form (Keycloak direct
    access grant). In production the frontend runs the full OIDC redirect flow with
    Keycloak; credentials are sent in the request BODY, never the URL."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{s.keycloak_url.rstrip('/')}/realms/{s.keycloak_realm}"
            "/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": s.keycloak_client_id,
                "client_secret": s.keycloak_client_secret,
                "username": body.username,
                "password": body.password,
                "scope": "openid profile email organization",
            },
        )
    if resp.status_code != 200:
        return JSONResponse(status_code=401, content={"error": "invalid_credentials",
                            "detail": resp.text})
    return resp.json()


class RegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    login_source: str


# ---- public self-service signup ---------------------------------------------
@app.post("/auth/register", tags=["auth"], status_code=201)
async def register(body: RegisterRequest):
    """Public signup: create the user (Keycloak + profile) via identity, then log
    them straight in. No bearer token exists yet — this is one of the few gateway
    routes that calls a downstream service directly rather than through the
    context-signed proxy (identity's /internal/register is exempt from the
    context requirement for exactly this reason)."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{s.identity_url}/internal/register", json=body.model_dump())
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return await dev_token(TokenRequest(username=body.email, password=body.password))


# ---- REST reverse proxy: /api/{service}/{path} ------------------------------
@app.api_route(
    "/api/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    tags=["proxy"],
)
async def gateway_proxy(service: str, path: str, request: Request):
    if service not in SERVICE_KEYS:
        return JSONResponse(status_code=404, content={"error": f"unknown service {service}"})
    ctx = request.state.tenant_context
    body = await request.body()
    return await proxy(
        service=service,
        path=path,
        method=request.method,
        ctx=ctx,
        headers=dict(request.headers),
        body=body,
        query=request.url.query,
    )
