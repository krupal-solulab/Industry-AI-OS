"""Service routing table + reverse proxy (streaming-aware)."""

from __future__ import annotations

import httpx
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

from ai_os_shared.auth import INTERNAL_HEADER, REQUEST_ID_HEADER, mint_context_header
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import TenantContext

# Public path segment -> downstream service base URL (resolved at call time).
SERVICE_KEYS = {
    "identity": "identity_url",
    "authz": "authz_url",
    "orchestrator": "orchestrator_url",
    "knowledge": "knowledge_url",
    "workflows": "workflows_url",
    "connectors": "connectors_url",
    "audit": "audit_url",
    "admin": "admin_url",
}

# Hop-by-hop headers we must not forward.
_STRIP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}


def target_base(service: str) -> str | None:
    attr = SERVICE_KEYS.get(service)
    if not attr:
        return None
    return getattr(get_settings(), attr)


async def proxy(
    service: str,
    path: str,
    method: str,
    ctx: TenantContext,
    headers: dict,
    body: bytes,
    query: str,
) -> Response:
    base = target_base(service)
    if base is None:
        return Response(content=f'{{"error":"unknown service {service}"}}', status_code=404,
                        media_type="application/json")

    url = f"{base.rstrip('/')}/{path}"
    if query:
        url = f"{url}?{query}"

    fwd = {k: v for k, v in headers.items() if k.lower() not in _STRIP}
    # Mint + attach the signed tenant context. This is what downstream trusts.
    fwd[INTERNAL_HEADER] = mint_context_header(ctx)
    if ctx.request_id:
        fwd[REQUEST_ID_HEADER] = ctx.request_id

    client = httpx.AsyncClient(timeout=None)
    req = client.build_request(method, url, headers=fwd, content=body or None)
    upstream = await client.send(req, stream=True)

    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP
    }
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=BackgroundTask(_aclose, upstream, client),
    )


async def _aclose(upstream: httpx.Response, client: httpx.AsyncClient) -> None:
    await upstream.aclose()
    await client.aclose()
