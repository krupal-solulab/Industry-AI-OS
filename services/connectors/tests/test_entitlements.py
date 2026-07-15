"""Unit tests for per-tenant connector entitlements (the opt-in allowlist).

These exercise the entitlement branching in the Connector Hub — list filtering,
invoke/configure gating, and the management endpoints — without a live stack. The
infra dependencies are stubbed: authz (`check_ctx`) and audit (`emit`) are no-ops,
and `tenant_session` is backed by a tiny in-memory store that speaks just enough of
the endpoints' SQL to keep the grant -> entitle -> invoke flow coherent.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from types import SimpleNamespace

import connectors.main as m
import pytest

from ai_os_shared.errors import AuthorizationError, NotFoundError, ValidationError
from ai_os_shared.tenant_context import TenantContext, reset_context, set_context
from ai_os_shared.types import Role

# A representative non-reference (Nango) connector that ships in the registry.
NANGO_KEY = "nango.google-mail"


class _Result:
    """Minimal stand-in for a SQLAlchemy Result — just needs to be iterable."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """In-memory backing for the two tables the endpoints touch, selected by SQL shape."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        s = self._store
        if "SELECT key, enabled, config FROM connectors" in sql:
            return _Result(
                [SimpleNamespace(key=k, enabled=v["enabled"], config=v["config"])
                 for k, v in s.connectors.items()]
            )
        if "SELECT connector_key FROM connector_entitlements" in sql:
            return _Result(
                [SimpleNamespace(connector_key=k)
                 for k, v in s.entitlements.items() if v["allowed"]]
            )
        if "SELECT connector_key, allowed, created_by, created_at" in sql:
            return _Result(
                [SimpleNamespace(connector_key=k, allowed=v["allowed"],
                                 created_by=v["created_by"], created_at=v["created_at"])
                 for k, v in sorted(s.entitlements.items())]
            )
        if "INSERT INTO connector_entitlements" in sql:
            k = params["key"]
            prior = s.entitlements.get(k, {})
            s.entitlements[k] = {
                "allowed": params.get("allowed", True),
                "created_by": params.get("by"),
                "created_at": prior.get("created_at")
                or dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
            }
            return _Result([])
        if "INSERT INTO connectors" in sql:
            s.connectors[params["key"]] = {"enabled": params["enabled"], "config": {}}
            return _Result([])
        return _Result([])


class _Store:
    def __init__(self) -> None:
        self.entitlements: dict[str, dict] = {}
        self.connectors: dict[str, dict] = {}


@pytest.fixture
def env(monkeypatch):
    """Bind a tenant context and stub authz/audit/DB; yield the in-memory store."""
    store = _Store()

    @asynccontextmanager
    async def _fake_tenant_session(ctx=None):
        yield _FakeSession(store)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(m, "tenant_session", _fake_tenant_session)
    monkeypatch.setattr(m, "check_ctx", _noop)
    monkeypatch.setattr(m, "emit", _noop)

    # Owner — entitlement management is owner/admin-only; management-endpoint tests need it.
    ctx = TenantContext(
        tenant_id="t-test", user_id="u-1", email="u@test.local", roles=[Role.OWNER]
    )
    token = set_context(ctx)
    try:
        yield store
    finally:
        reset_context(token)


async def test_list_default_hides_non_entitled(env):
    items = await m.list_connectors()
    keys = {i["key"] for i in items}
    # Only the always-usable reference connector shows before any grant.
    assert keys == {"echo"}
    assert all(i["entitled"] for i in items)


async def test_list_all_shows_full_catalog_with_flags(env):
    items = await m.list_connectors(all=True)
    by_key = {i["key"]: i for i in items}
    # Full catalog is returned regardless of entitlement.
    assert len(by_key) == len(m.all_connectors())
    assert by_key["echo"]["entitled"] is True  # reference always entitled
    assert by_key[NANGO_KEY]["entitled"] is False  # not granted yet


async def test_grant_defaults_grants_catalog(env):
    result = await m.grant_default_entitlements()
    expected = [c.key for c in m.all_connectors() if c.kind != "reference"]
    assert result["count"] == len(expected)
    assert set(result["granted"]) == set(expected)
    assert "echo" not in result["granted"]  # reference is never in the allowlist

    # After grant every non-reference connector shows in the default (filtered) list.
    listed = {i["key"] for i in await m.list_connectors()}
    assert set(expected) | {"echo"} == listed


async def test_grant_defaults_is_idempotent(env):
    first = await m.grant_default_entitlements()
    second = await m.grant_default_entitlements()
    assert first == second
    assert len(env.entitlements) == first["count"]


async def test_invoke_blocked_until_entitled_then_enabled(env):
    body = m.InvokeBody(tool="GET", arguments={"endpoint": "/messages/1"})

    # 1. Not entitled -> entitlement gate blocks.
    with pytest.raises(ValidationError, match="not entitled"):
        await m.invoke(NANGO_KEY, body)

    # 2. Grant -> entitlement passes, but the connector is still not enabled.
    await m.grant_default_entitlements()
    with pytest.raises(ValidationError, match="not enabled"):
        await m.invoke(NANGO_KEY, body)

    # 3. Enable it -> invoke now runs (sandbox result, no network/creds).
    await m.configure(NANGO_KEY, m.ConfigureBody(enabled=True, config={}))
    out = await m.invoke(NANGO_KEY, body)
    assert out["connector"] == NANGO_KEY
    assert out["result"]["_sandbox"] is True


async def test_echo_always_usable(env):
    # The reference connector needs neither entitlement nor enablement.
    out = await m.invoke("echo", m.InvokeBody(tool="ping", arguments={}))
    assert out["result"] == {"pong": True}


async def test_configure_enable_requires_entitlement(env):
    # Enabling a connector the tenant isn't entitled to is rejected...
    with pytest.raises(ValidationError, match="not entitled"):
        await m.configure(NANGO_KEY, m.ConfigureBody(enabled=True, config={}))
    # ...but disabling it is always allowed.
    res = await m.configure(NANGO_KEY, m.ConfigureBody(enabled=False, config={}))
    assert res == {"key": NANGO_KEY, "enabled": False}


async def test_set_and_list_entitlements(env):
    with pytest.raises(NotFoundError):
        await m.set_entitlement("does-not-exist", m.EntitlementBody(allowed=True))

    res = await m.set_entitlement(NANGO_KEY, m.EntitlementBody(allowed=True))
    assert res == {"connector_key": NANGO_KEY, "allowed": True}

    rows = await m.list_entitlements()
    assert len(rows) == 1
    row = rows[0]
    assert row["connector_key"] == NANGO_KEY
    assert row["allowed"] is True
    assert row["created_by"] == "u-1"
    assert isinstance(row["created_at"], str)  # isoformat serialization


async def test_entitlement_management_requires_admin(env):
    # A non-admin (member) may NOT grant/revoke or run grant-defaults...
    member = TenantContext(tenant_id="t-test", user_id="u-2", roles=[Role.MEMBER])
    token = set_context(member)
    try:
        with pytest.raises(AuthorizationError):
            await m.set_entitlement(NANGO_KEY, m.EntitlementBody(allowed=True))
        with pytest.raises(AuthorizationError):
            await m.grant_default_entitlements()
        # ...but reading the (empty) list is still allowed for any member.
        assert await m.list_entitlements() == []
    finally:
        reset_context(token)
