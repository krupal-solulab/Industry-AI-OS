"""Tests for user-authored workflow persistence (the visual-builder backend).

Two layers:

1. Pure-logic tests (no DB): the normalization/validation helpers that shape a
   builder-authored definition before it is stored — force the reserved 'custom' pack,
   require a `key`, run full schema validation, and build the FE graph spec.

2. DB-backed integration (skipped when Postgres/the 0004 schema is unavailable): the full
   CRUD + run lifecycle against a real tenant-scoped, RLS-protected session — create →
   list (source='user') → run via the generic engine (load_definition resolves it from
   the DB) → update → delete, plus the guard that a source='seed' row can never be
   mutated or deleted through the user CRUD.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError
from workflows import pack_runtime

from ai_os_shared.tenant_context import TenantContext, reset_context, set_context

# --------------------------------------------------------------------------- fixtures
LINEAR_FLOW = {
    "key": "greeting_flow",
    "business_goal": "Greet a user by name (builder smoke flow).",
    "trigger": {"type": "manual"},
    "inputs": [{"key": "name", "type": "string", "required": True}],
    "steps": [
        {"id": "t1", "type": "transform", "config": {"greeting": "Hello {{ inputs.name }}"}},
    ],
    "outputs": [{"key": "greeting", "from": "{{ steps.t1.out.greeting }}"}],
}


# ------------------------------------------------------------------------- pure logic
def test_prepare_forces_custom_pack_and_keeps_key():
    wf = pack_runtime._prepare_user_definition(dict(LINEAR_FLOW))
    assert wf.pack == pack_runtime.USER_PACK_KEY == "custom"
    assert wf.key == "greeting_flow"


def test_prepare_locks_key_from_argument():
    wf = pack_runtime._prepare_user_definition(dict(LINEAR_FLOW), "renamed")
    assert wf.key == "renamed"


def test_prepare_requires_key():
    body = {k: v for k, v in LINEAR_FLOW.items() if k != "key"}
    with pytest.raises(ValueError):
        pack_runtime._prepare_user_definition(body)


def test_prepare_rejects_invalid_definition():
    bad = {
        "key": "bad",
        "trigger": {"type": "manual"},
        "connectors_required": [],
        # connector.call using a connector not declared -> schema ValidationError
        "steps": [{"id": "s", "type": "connector.call", "config": {"connector": "nango.x"}}],
    }
    with pytest.raises(ValidationError):
        pack_runtime._prepare_user_definition(bad)


def test_definition_spec_shape():
    wf = pack_runtime._prepare_user_definition(dict(LINEAR_FLOW))
    spec = pack_runtime._definition_spec(wf, "user")
    assert spec == {
        "pack_key": "custom",
        "workflow_key": "greeting_flow",
        "name": "Greet a user by name (builder smoke flow).",
        "description": "Greet a user by name (builder smoke flow).",
        "trigger": "manual",
        "connectors_required": [],
        "steps": [{"id": "t1", "type": "transform", "name": "t1"}],
        "latest_status": None,
        "latest_run_id": None,
        "source": "user",
    }


# --------------------------------------------------------------------- DB integration
def _test_ctx() -> TenantContext:
    return TenantContext(tenant_id="test-builder-tenant", user_id="tester@aios.local")


async def _db_available(ctx) -> bool:
    """True only if Postgres is reachable AND migration 0004 (the `source` column) ran."""
    if os.getenv("AIOS_TEST_DATABASE_URL"):
        os.environ.setdefault("DATABASE_URL", os.environ["AIOS_TEST_DATABASE_URL"])
    try:
        from sqlalchemy import text

        from ai_os_shared.db import tenant_session

        async with tenant_session(ctx) as s:
            await s.execute(text("SELECT source FROM workflow_definitions WHERE false"))
        return True
    except Exception:
        return False


@pytest.fixture
async def db_ctx():
    ctx = _test_ctx()
    token = set_context(ctx)
    if not await _db_available(ctx):
        reset_context(token)
        pytest.skip("Postgres/0004 schema unavailable (set AIOS_TEST_DATABASE_URL to run)")
    try:
        yield ctx
    finally:
        # Best-effort cleanup of anything this test tenant wrote.
        from sqlalchemy import text

        from ai_os_shared.db import tenant_session

        async with tenant_session(ctx) as s:
            await s.execute(text("DELETE FROM workflow_step_runs WHERE tenant_id = :t"),
                            {"t": ctx.tenant_id})
            await s.execute(text("DELETE FROM workflow_runs WHERE tenant_id = :t"),
                            {"t": ctx.tenant_id})
            await s.execute(text("DELETE FROM workflow_definitions WHERE tenant_id = :t"),
                            {"t": ctx.tenant_id})
        reset_context(token)


async def test_create_list_run_update_delete_lifecycle(db_ctx):
    ctx = db_ctx

    # create
    spec = await pack_runtime.create_definition(ctx, dict(LINEAR_FLOW))
    assert spec["source"] == "user"
    assert spec["pack_key"] == "custom"
    assert spec["workflow_key"] == "greeting_flow"

    # list shows it with source='user'
    listed = await pack_runtime.list_definitions(ctx)
    ours = [d for d in listed if d["workflow_key"] == "greeting_flow" and d["pack_key"] == "custom"]
    assert len(ours) == 1
    assert ours[0]["source"] == "user"
    # seed flows (shipped packs) are still listed and tagged 'seed'
    assert all("source" in d for d in listed)
    assert any(d["source"] == "seed" for d in listed)

    # run it via the normal run path — load_definition must resolve it from the DB
    result = await pack_runtime.start_run(
        ctx, None, "run-test-1", "custom", "greeting_flow", {"name": "world"}
    )
    assert result["status"] == "completed"
    assert result["outputs"]["greeting"] == "Hello world"

    # latest run status now surfaces in the list
    relisted = await pack_runtime.list_definitions(ctx)
    ours = next(d for d in relisted if d["workflow_key"] == "greeting_flow")
    assert ours["latest_status"] == "completed"

    # update
    updated_body = dict(LINEAR_FLOW)
    updated_body["business_goal"] = "Updated goal"
    spec2 = await pack_runtime.update_definition(ctx, "greeting_flow", updated_body)
    assert spec2["name"] == "Updated goal"

    # delete
    await pack_runtime.delete_definition(ctx, "greeting_flow")
    after = await pack_runtime.list_definitions(ctx)
    assert not any(
        d["workflow_key"] == "greeting_flow" and d["pack_key"] == "custom" for d in after
    )


async def test_update_missing_user_flow_raises(db_ctx):
    with pytest.raises(KeyError):
        await pack_runtime.update_definition(db_ctx, "does-not-exist", dict(LINEAR_FLOW))


async def test_delete_missing_user_flow_raises(db_ctx):
    with pytest.raises(KeyError):
        await pack_runtime.delete_definition(db_ctx, "does-not-exist")


async def test_seed_row_cannot_be_mutated_or_deleted(db_ctx):
    """A source='seed' row is off-limits to the user CRUD, even under the 'custom' pack."""
    ctx = db_ctx
    from sqlalchemy import text

    from ai_os_shared.db import tenant_session

    # Plant an artificial seed row under the reserved pack.
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO workflow_definitions
                   (tenant_id, pack_key, workflow_key, version, definition, source)
                   VALUES (:t, 'custom', 'seeded_one', '1.0.0',
                           CAST(:d AS jsonb), 'seed')"""
            ),
            {"t": ctx.tenant_id, "d": '{"key": "seeded_one", "pack": "custom"}'},
        )

    with pytest.raises(KeyError):
        await pack_runtime.update_definition(ctx, "seeded_one", dict(LINEAR_FLOW))
    with pytest.raises(KeyError):
        await pack_runtime.delete_definition(ctx, "seeded_one")

    # And it is still there (guard did not silently clobber it).
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT source FROM workflow_definitions "
                    "WHERE tenant_id = :t AND workflow_key = 'seeded_one'"
                ),
                {"t": ctx.tenant_id},
            )
        ).mappings().first()
    assert row is not None and row["source"] == "seed"
