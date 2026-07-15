"""Unit tests for per-tenant connector entitlements (the opt-in allowlist).

These exercise the entitlement branching in the Connector Hub — list filtering,
invoke/configure gating, and the management endpoints — without a live stack. The
infra dependencies are stubbed: authz (`check_ctx`) and audit (`emit`) are no-ops,
and `tenant_session` is backed by a tiny in-memory store that speaks just enough of
the endpoints' SQL to keep the grant -> entitle -> invoke flow coherent.
"""

from __future__ import annotations

import datetime as dt
import uuid
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
    """Minimal stand-in for a SQLAlchemy Result — iterable, with all/first/rowcount."""

    def __init__(self, rows: list, rowcount: int | None = None) -> None:
        self._rows = rows
        self.rowcount = rowcount if rowcount is not None else len(rows)

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


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
        if "SELECT connector_key, allowed FROM connector_entitlements" in sql:
            return _Result(
                [SimpleNamespace(connector_key=k, allowed=v["allowed"])
                 for k, v in s.entitlements.items()]
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
        # ---- connector_access_requests ----
        if "INSERT INTO connector_access_requests" in sql:
            key = params["key"]
            if any(r["connector_key"] == key and r["status"] == "pending"
                   for r in s.requests.values()):
                return _Result([])  # ON CONFLICT (pending) DO NOTHING
            rid = str(uuid.uuid4())
            s.requests[rid] = {
                "id": rid, "connector_key": key, "status": "pending",
                "requested_by": params.get("by"), "note": params.get("note"),
                "decided_by": None,
                "created_at": dt.datetime(2026, 7, 15, tzinfo=dt.UTC), "decided_at": None,
            }
            return _Result([])
        if "SELECT id, connector_key, status, requested_by" in sql:
            rows = list(s.requests.values())
            if "AND status = :status" in sql:
                rows = [r for r in rows if r["status"] == params.get("status")]
            return _Result([SimpleNamespace(**r) for r in rows])
        if "SELECT connector_key FROM connector_access_requests" in sql:
            r = s.requests.get(params["id"])
            if r and r["status"] == "pending":
                return _Result([SimpleNamespace(connector_key=r["connector_key"])])
            return _Result([])
        if "SET status = 'approved'" in sql:
            r = s.requests.get(params["id"])
            if r:
                r["status"] = "approved"
                r["decided_by"] = params.get("by")
            return _Result([], rowcount=1 if r else 0)
        if "SET status = 'rejected'" in sql:
            r = s.requests.get(params["id"])
            if r and r["status"] == "pending":
                r["status"] = "rejected"
                r["decided_by"] = params.get("by")
                return _Result([], rowcount=1)
            return _Result([], rowcount=0)
        return _Result([])


class _Store:
    def __init__(self) -> None:
        self.entitlements: dict[str, dict] = {}
        self.connectors: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}


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


async def test_list_unrestricted_shows_all(env):
    # A tenant with NO entitlement rows is unrestricted → the whole catalog is available.
    items = await m.list_connectors()
    assert {i["key"] for i in items} == {c.key for c in m.all_connectors()}
    assert all(i["entitled"] for i in items)


async def test_list_all_flags_everything_when_unrestricted(env):
    items = await m.list_connectors(all=True)
    by_key = {i["key"]: i for i in items}
    assert len(by_key) == len(m.all_connectors())
    assert by_key["echo"]["entitled"] is True
    assert by_key[NANGO_KEY]["entitled"] is True  # unrestricted → everything entitled


async def test_granting_one_restricts_to_allowlist(env):
    # The first grant flips the tenant to restricted: now only the granted connector
    # (plus the always-usable reference) is visible.
    await m.set_entitlement(NANGO_KEY, m.EntitlementBody(allowed=True))
    assert {i["key"] for i in await m.list_connectors()} == {NANGO_KEY, "echo"}


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


async def test_invoke_blocked_when_restricted_off_allowlist(env):
    body = m.InvokeBody(tool="GET", arguments={"endpoint": "/messages/1"})

    # Restrict the tenant to a DIFFERENT connector so NANGO is off the allowlist.
    await m.set_entitlement("composio", m.EntitlementBody(allowed=True))
    with pytest.raises(ValidationError, match="not entitled"):
        await m.invoke(NANGO_KEY, body)

    # Grant NANGO -> entitled, but still not enabled.
    await m.set_entitlement(NANGO_KEY, m.EntitlementBody(allowed=True))
    with pytest.raises(ValidationError, match="not enabled"):
        await m.invoke(NANGO_KEY, body)

    # Enable it -> invoke now runs (sandbox result, no network/creds).
    await m.configure(NANGO_KEY, m.ConfigureBody(enabled=True, config={}))
    out = await m.invoke(NANGO_KEY, body)
    assert out["connector"] == NANGO_KEY
    assert out["result"]["_sandbox"] is True


async def test_echo_always_usable(env):
    # The reference connector needs neither entitlement nor enablement.
    out = await m.invoke("echo", m.InvokeBody(tool="ping", arguments={}))
    assert out["result"] == {"pong": True}


async def test_configure_enable_requires_entitlement_when_restricted(env):
    # Restrict the tenant to a different connector so NANGO is off-allowlist...
    await m.set_entitlement("composio", m.EntitlementBody(allowed=True))
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


async def test_access_request_flow(env):
    # Restrict the tenant (grant a different connector) so NANGO is off-allowlist.
    await m.set_entitlement("composio", m.EntitlementBody(allowed=True))

    # A member requests access to NANGO.
    member = TenantContext(tenant_id="t-test", user_id="req-user", roles=[Role.MEMBER])
    tok = set_context(member)
    try:
        res = await m.request_access(NANGO_KEY, m.AccessRequestBody(note="need mail"))
        assert res["status"] == "pending"
        # A second request is a no-op — still exactly one pending.
        await m.request_access(NANGO_KEY, m.AccessRequestBody())
        pending = [r for r in await m.list_access_requests(status="pending")
                   if r["connector_key"] == NANGO_KEY]
        assert len(pending) == 1
    finally:
        reset_context(tok)

    # Owner approves -> request approved AND the entitlement is granted.
    rid = next(r["id"] for r in await m.list_access_requests(status="pending")
               if r["connector_key"] == NANGO_KEY)
    out = await m.approve_access_request(rid)
    assert out["status"] == "approved"
    assert NANGO_KEY in {i["key"] for i in await m.list_connectors()}


async def test_request_access_rejected_when_already_available(env):
    # Unrestricted tenant -> everything is already available -> nothing to request.
    with pytest.raises(ValidationError, match="already available"):
        await m.request_access(NANGO_KEY, m.AccessRequestBody())


async def test_access_decisions_require_admin(env):
    await m.set_entitlement("composio", m.EntitlementBody(allowed=True))
    await m.request_access(NANGO_KEY, m.AccessRequestBody())
    rid = next(r["id"] for r in await m.list_access_requests(status="pending")
               if r["connector_key"] == NANGO_KEY)

    member = TenantContext(tenant_id="t-test", user_id="m", roles=[Role.MEMBER])
    tok = set_context(member)
    try:
        with pytest.raises(AuthorizationError):
            await m.approve_access_request(rid)
        with pytest.raises(AuthorizationError):
            await m.reject_access_request(rid)
    finally:
        reset_context(tok)

    # Owner can reject.
    assert (await m.reject_access_request(rid))["status"] == "rejected"


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
