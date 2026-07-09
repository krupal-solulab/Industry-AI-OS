"""Shared FastAPI application factory.

Every service builds its app with `create_app(...)` so they all get identical:
  - telemetry + structured logging
  - platform error → HTTP mapping
  - request-id propagation
  - (for downstream services) verification of the gateway-signed tenant context,
    bound into the contextvar for the request's lifetime
  - /healthz and /readyz endpoints

The gateway itself sets `trust_gateway_context=False` because it MINTS the context
from a JWT rather than receiving it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ai_os_shared.auth import INTERNAL_HEADER, REQUEST_ID_HEADER, verify_context_header
from ai_os_shared.errors import PlatformError, TenantContextError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.telemetry import setup_telemetry
from ai_os_shared.tenant_context import reset_context, set_context

log = structlog.get_logger("aios.app")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=rid)
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Verify the gateway-signed context header and bind it for the request.

    Unauthenticated paths (health, docs, internal audit ingest) are exempt.
    """

    # `/internal/*` is server-to-server (e.g. audit ingest) and is never exposed
    # through the gateway; it is reachable only on the private network.
    EXEMPT_PREFIXES = (
        "/healthz",
        "/readyz",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/internal",
    )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(self.EXEMPT_PREFIXES):
            return await call_next(request)
        header = request.headers.get(INTERNAL_HEADER)
        if not header:
            return JSONResponse(
                status_code=400,
                content={"code": "tenant_context_error", "message": "Missing internal context"},
            )
        try:
            ctx = verify_context_header(header)
            ctx = ctx.model_copy(update={"request_id": getattr(request.state, "request_id", None)})
        except TenantContextError as exc:
            return JSONResponse(
                status_code=exc.status_code, content={"code": exc.code, "message": exc.message}
            )
        token = set_context(ctx)
        try:
            return await call_next(request)
        finally:
            reset_context(token)


def create_app(
    *,
    service_name: str,
    title: str,
    trust_gateway_context: bool = True,
    health_registry: HealthRegistry | None = None,
    lifespan: Callable | None = None,
) -> FastAPI:
    app = FastAPI(title=title, version="0.1.0", lifespan=lifespan)
    setup_telemetry(app, service_name)

    app.add_middleware(RequestIdMiddleware)
    if trust_gateway_context:
        app.add_middleware(TenantContextMiddleware)

    registry = health_registry or HealthRegistry(service_name)
    app.state.health = registry

    @app.exception_handler(PlatformError)
    async def _platform_error_handler(_: Request, exc: PlatformError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.get("/healthz", tags=["health"], include_in_schema=False)
    async def healthz():
        return {"status": "ok", "service": service_name}

    @app.get("/readyz", tags=["health"], include_in_schema=False)
    async def readyz():
        report = await registry.report()
        code = 200 if report.status.value != "down" else 503
        return JSONResponse(status_code=code, content=report.model_dump(mode="json"))

    return app
