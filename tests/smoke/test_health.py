"""Every application service must report healthy."""

import httpx
import pytest

from .conftest import SERVICE_PORTS, requires_stack


@requires_stack
@pytest.mark.parametrize("service,port", SERVICE_PORTS.items())
def test_service_healthz(service: str, port: int):
    resp = httpx.get(f"http://localhost:{port}/healthz", timeout=5)
    assert resp.status_code == 200, f"{service} unhealthy"


@requires_stack
@pytest.mark.parametrize("service,port", SERVICE_PORTS.items())
def test_service_readyz(service: str, port: int):
    resp = httpx.get(f"http://localhost:{port}/readyz", timeout=10)
    # readyz may report degraded (e.g. no LLM key) but must not be down.
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
