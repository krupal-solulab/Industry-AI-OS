"""Smoke-test fixtures. These run against a LIVE stack (`make up` first).

If the gateway isn't reachable, tests skip rather than fail — so `make smoke` on a
machine without the stack running is a no-op, not a red build.
"""

from __future__ import annotations

import httpx
import pytest

GATEWAY = "http://localhost:8000"

# Host ports published by deploy/docker-compose.yml.
SERVICE_PORTS = {
    "gateway": 8000,
    "identity": 8001,
    "authz": 8002,
    "orchestrator": 8003,
    "knowledge": 8004,
    "workflows": 8005,
    "connectors": 8006,
    "audit": 8007,
    "admin": 8008,
}


def _stack_up() -> bool:
    try:
        return httpx.get(f"{GATEWAY}/healthz", timeout=2).status_code == 200
    except Exception:
        return False


requires_stack = pytest.mark.skipif(not _stack_up(), reason="stack not running (run `make up`)")


@pytest.fixture(scope="session")
def gateway() -> str:
    return GATEWAY


def _token(role: str) -> str:
    resp = httpx.post(
        f"{GATEWAY}/auth/token",
        json={"username": f"{role}@demo.aios.local", "password": "Passw0rd!"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture
def owner_token() -> str:
    return _token("owner")


@pytest.fixture
def viewer_token() -> str:
    return _token("viewer")


@pytest.fixture
def auth_client(owner_token):
    with httpx.Client(
        base_url=GATEWAY, headers={"Authorization": f"Bearer {owner_token}"}, timeout=60
    ) as client:
        yield client
