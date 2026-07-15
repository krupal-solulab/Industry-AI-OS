"""DB-backed executor for Workflow Pack definitions.

Runs a validated `WorkflowDefinition` via the generic `WorkflowEngine` with real injected
dependencies — LLM (LiteLLM), the Connector Hub (over HTTP, forwarding the signed tenant
context), the Knowledge service (RAG), prompt files, and human approval — persisting run,
step, and approval state to Postgres.

Human approval is a durable pause: the run's context is saved with status
`awaiting_approval` and an `approval_tasks` row is created; a later approve/reject
rehydrates the context and resumes the engine (which skips already-completed steps).

NOTE (ADR-0006): this is the durable executor for the demo. The suspend/resume + persisted
context already survive the human-in-the-loop wait. Wrapping it in a Temporal `PackWorkflow`
for automatic retries / mid-step process-restart durability is a follow-up — the engine and
this module are structured so that swap is additive.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from sqlalchemy import text

from ai_os_shared.auth import INTERNAL_HEADER
from ai_os_shared.db import tenant_session
from ai_os_shared.llm import get_llm
from ai_os_shared.settings import get_settings
from ai_os_shared.workflow.engine import PendingApproval, RunContext, WorkflowEngine
from ai_os_shared.workflow.registry import load_all_packs, load_pack
from ai_os_shared.workflow.schema import Step, WorkflowDefinition, validate_definition
from workflows.step_handlers import build_handlers

# Reserved pack_key for user-authored ("builder") flows. These live only in the DB
# (source='user'); every shipped repo pack uses its own key and source='seed'.
USER_PACK_KEY = "custom"


def _packs_root() -> Path:
    for candidate in (os.getenv("AIOS_PACKS_DIR"), "/app/packs", "packs"):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return Path("/app/packs")


def _as_dict(definition) -> dict:
    """A jsonb column comes back as a dict on asyncpg, but tolerate a str just in case."""
    return definition if isinstance(definition, dict) else json.loads(definition)


async def load_definition(ctx, pack_key: str, workflow_key: str) -> WorkflowDefinition:
    """Resolve a workflow definition DB-first, then disk.

    User-authored flows exist only in the tenant's DB registry (pack_key='custom'),
    while shipped packs live in the repo pack files. Querying the DB first makes builder
    flows runnable through the same generic engine; if the (tenant, pack, workflow) row
    is absent we fall back to the on-disk pack. Raises KeyError if neither has it.
    """
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT definition FROM workflow_definitions "
                    "WHERE tenant_id = :tid AND pack_key = :pk AND workflow_key = :wf"
                ),
                {"tid": ctx.tenant_id, "pk": pack_key, "wf": workflow_key},
            )
        ).mappings().first()
    if row is not None:
        return validate_definition(_as_dict(row["definition"]))
    # Not in the DB — fall back to the shipped pack files on disk.
    pack_dir = _packs_root() / pack_key
    if not (pack_dir / "pack.json").exists():
        raise KeyError(f"workflow '{workflow_key}' not found in pack '{pack_key}'")
    _manifest, defs = load_pack(pack_dir)
    if workflow_key not in defs:
        raise KeyError(f"workflow '{workflow_key}' not found in pack '{pack_key}'")
    return defs[workflow_key]


def _display_name(wf: WorkflowDefinition, source: str) -> str:
    """A short, human title for the flow. User-built flows carry the name the author typed
    (in `business_goal`); seeded packs put a long goal *sentence* there, so title-case the
    key instead — 'invoice_verification' → 'Invoice Verification'."""
    goal = (wf.business_goal or "").strip()
    if source == "user" and goal:
        return goal
    return wf.key.replace("_", " ").replace("-", " ").strip().title() or wf.key


def _definition_spec(wf: WorkflowDefinition, source: str, latest: dict | None = None) -> dict:
    """The graph-spec shape the FE flow visualization consumes (shared by list + CRUD)."""
    return {
        "pack_key": wf.pack,
        "workflow_key": wf.key,
        "name": _display_name(wf, source),
        "description": (wf.business_goal or "").strip(),
        "trigger": wf.trigger.type.value if wf.trigger else "manual",
        "connectors_required": wf.connectors_required,
        "steps": [
            {"id": st.id, "type": st.type.value, "name": st.name or st.id}
            for st in wf.steps
        ],
        "latest_status": latest["status"] if latest else None,
        "latest_run_id": latest["run_id"] if latest else None,
        "source": source,
    }


def _prepare_user_definition(definition: dict, workflow_key: str | None = None):
    """Normalize + validate a builder-authored definition.

    Forces the reserved 'custom' pack, requires (or locks) the workflow key, and runs
    full schema validation. Returns the validated `WorkflowDefinition`; raises ValueError
    when the key is missing and pydantic ValidationError on an invalid definition.
    """
    data = dict(definition)
    data["pack"] = USER_PACK_KEY
    if workflow_key is not None:
        data["key"] = workflow_key
    if not data.get("key"):
        raise ValueError("workflow definition requires a 'key'")
    return validate_definition(data)


def _prompt_loader(pack_key: str):
    prompts = _packs_root() / pack_key / "prompts"

    def load(name: str) -> str:
        return (prompts / f"{name}.md").read_text(encoding="utf-8")

    return load


def _handlers(pack_key: str, header: str | None, decisions: dict):
    """Build engine step handlers with live dependencies for this run."""

    async def llm_chat(messages, model):
        return await get_llm().chat(messages, model=model)

    async def invoke_connector(connector: str, tool: str, arguments: dict) -> dict:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.connectors_url.rstrip('/')}/connectors/{connector}/invoke",
                    headers={INTERNAL_HEADER: header} if header else {},
                    json={"tool": tool, "arguments": arguments},
                )
                resp.raise_for_status()
                return resp.json().get("result", {})
        except httpx.HTTPError as exc:
            return {"status": "error", "connector": connector, "error": str(exc)}

    async def retrieve(query: str, top_k: int) -> dict:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{settings.knowledge_url.rstrip('/')}/retrieve",
                    headers={INTERNAL_HEADER: header} if header else {},
                    json={"query": query, "top_k": top_k},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError:
            return {"results": []}

    async def wait_for_approval(step: Step, cfg: dict) -> dict:
        if step.id in decisions:
            return decisions[step.id]
        raise PendingApproval(step.id)

    return build_handlers(
        llm_chat=llm_chat,
        invoke_connector=invoke_connector,
        load_prompt=_prompt_loader(pack_key),
        retrieve=retrieve,
        wait_for_approval=wait_for_approval,
    )


# --------------------------------------------------------------------- persistence
async def _create_run(ctx, run_id, pack_key, wf_key, inputs) -> None:
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO workflow_runs
                   (tenant_id, run_id, pack_key, workflow_key, status, context, created_by)
                   VALUES (:tid, :rid, :pack, :wf, 'running', :ctx, :by)"""
            ),
            {"tid": ctx.tenant_id, "rid": run_id, "pack": pack_key, "wf": wf_key,
             "ctx": json.dumps({"inputs": inputs, "steps": {}}), "by": ctx.user_id},
        )


async def _save_run(ctx, run_id, status, context, current_step=None) -> None:
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                "UPDATE workflow_runs SET status = :st, context = :ctx, "
                "current_step = :cur, updated_at = now() WHERE run_id = :rid"
            ),
            {"st": status, "ctx": json.dumps(context, default=str),
             "cur": current_step, "rid": run_id},
        )


async def _persist_steps(ctx, run_id, steps: dict) -> None:
    """Mirror the engine context's per-step outputs into workflow_step_runs (idempotent)."""
    async with tenant_session(ctx) as s:
        for step_id, data in steps.items():
            await s.execute(
                text(
                    """INSERT INTO workflow_step_runs
                       (tenant_id, run_id, step_id, type, status, output, ended_at)
                       VALUES (:tid, :rid, :sid, 'step', 'completed', :out, now())
                       ON CONFLICT DO NOTHING"""
                ),
                {"tid": ctx.tenant_id, "rid": run_id, "sid": step_id,
                 "out": json.dumps(data.get("out", {}), default=str)},
            )


async def _create_approval(ctx, run_id, step_id, approver_role, summary) -> None:
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO approval_tasks
                   (tenant_id, run_id, step_id, approver_role, status, comment)
                   VALUES (:tid, :rid, :sid, :role, 'pending', :summary)"""
            ),
            {"tid": ctx.tenant_id, "rid": run_id, "sid": step_id,
             "role": approver_role, "summary": (summary or "")[:2000]},
        )


async def seed_tenant_packs(ctx) -> int:
    """Load every repo pack and upsert it into this tenant's DB registry
    (workflow_packs + workflow_definitions). Idempotent."""
    packs = load_all_packs(_packs_root())
    count = 0
    async with tenant_session(ctx) as s:
        for pack_key, (manifest, defs) in packs.items():
            await s.execute(
                text(
                    """INSERT INTO workflow_packs
                       (tenant_id, pack_key, industry, version, manifest)
                       VALUES (:tid, :pk, :ind, :ver, :man)
                       ON CONFLICT (tenant_id, pack_key)
                       DO UPDATE SET industry = :ind, version = :ver, manifest = :man"""
                ),
                {"tid": ctx.tenant_id, "pk": pack_key, "ind": manifest.industry,
                 "ver": manifest.version, "man": json.dumps(manifest.model_dump())},
            )
            for wf_key, wf in defs.items():
                await s.execute(
                    text(
                        """INSERT INTO workflow_definitions
                           (tenant_id, pack_key, workflow_key, version, definition)
                           VALUES (:tid, :pk, :wf, :ver, :def)
                           ON CONFLICT (tenant_id, pack_key, workflow_key)
                           DO UPDATE SET version = :ver, definition = :def"""
                    ),
                    {"tid": ctx.tenant_id, "pk": pack_key, "wf": wf_key, "ver": wf.version,
                     "def": json.dumps(wf.model_dump(by_alias=True))},
                )
                count += 1
    return count


async def list_definitions(ctx) -> list[dict]:
    """Every runnable workflow definition as a graph spec for the FE flow visualization:
    steps (id/type/name), required connectors, trigger, and the latest run's status.

    Returns the shipped/seeded packs from disk (source='seed') PLUS this tenant's
    builder-authored flows from the DB (source='user'), both in the same spec shape and
    both annotated with their latest run status.
    """
    packs = load_all_packs(_packs_root())
    async with tenant_session(ctx) as s:
        run_rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT ON (pack_key, workflow_key) pack_key, workflow_key, "
                    "status, run_id FROM workflow_runs "
                    "ORDER BY pack_key, workflow_key, updated_at DESC"
                )
            )
        ).mappings().all()
        user_rows = (
            await s.execute(
                text(
                    "SELECT pack_key, workflow_key, definition FROM workflow_definitions "
                    "WHERE tenant_id = :tid AND source = 'user'"
                ),
                {"tid": ctx.tenant_id},
            )
        ).mappings().all()
    latest = {(r["pack_key"], r["workflow_key"]): r for r in run_rows}
    out: list[dict] = []
    for _pack_key, (_manifest, defs) in packs.items():
        for _wf_key, wf in defs.items():
            out.append(_definition_spec(wf, "seed", latest.get((wf.pack, wf.key))))
    for row in user_rows:
        wf = validate_definition(_as_dict(row["definition"]))
        lr = latest.get((row["pack_key"], row["workflow_key"]))
        out.append(_definition_spec(wf, "user", lr))
    return out


# --------------------------------------------------------------- user-authored CRUD
async def create_definition(ctx, definition: dict) -> dict:
    """Create (or overwrite) a user-authored flow under the reserved 'custom' pack.

    The definition's `pack` is forced to 'custom' and `key` (required) becomes the
    workflow_key. Upserts by (tenant, 'custom', key); the WHERE guard refuses to clobber
    a source='seed' row. Returns the graph spec (source='user').
    """
    wf = _prepare_user_definition(definition)
    payload = json.dumps(wf.model_dump(by_alias=True))
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO workflow_definitions
                   (tenant_id, pack_key, workflow_key, version, definition, source, created_by)
                   VALUES (:tid, :pk, :wf, :ver, CAST(:def AS jsonb), 'user', :by)
                   ON CONFLICT (tenant_id, pack_key, workflow_key)
                   DO UPDATE SET definition = CAST(:def AS jsonb), version = :ver,
                                 source = 'user', created_by = :by, updated_at = now()
                   WHERE workflow_definitions.source = 'user'"""
            ),
            {"tid": ctx.tenant_id, "pk": USER_PACK_KEY, "wf": wf.key,
             "ver": wf.version, "def": payload, "by": ctx.user_id},
        )
    return _definition_spec(wf, "user")


async def update_definition(ctx, workflow_key: str, definition: dict) -> dict:
    """Replace an existing user-authored flow. Raises KeyError if no source='user' row
    exists for (tenant, 'custom', workflow_key) — a seed flow can never be mutated."""
    wf = _prepare_user_definition(definition, workflow_key)
    payload = json.dumps(wf.model_dump(by_alias=True))
    async with tenant_session(ctx) as s:
        result = await s.execute(
            text(
                """UPDATE workflow_definitions
                   SET definition = CAST(:def AS jsonb), version = :ver, updated_at = now()
                   WHERE tenant_id = :tid AND pack_key = :pk
                     AND workflow_key = :wf AND source = 'user'"""
            ),
            {"tid": ctx.tenant_id, "pk": USER_PACK_KEY, "wf": workflow_key,
             "ver": wf.version, "def": payload},
        )
    if result.rowcount == 0:
        raise KeyError(f"user workflow '{workflow_key}' not found")
    return _definition_spec(wf, "user")


async def delete_definition(ctx, workflow_key: str) -> None:
    """Delete a user-authored flow. Raises KeyError if no source='user' row matches
    (tenant, 'custom', workflow_key) — a seed flow can never be deleted."""
    async with tenant_session(ctx) as s:
        result = await s.execute(
            text(
                "DELETE FROM workflow_definitions "
                "WHERE tenant_id = :tid AND pack_key = :pk "
                "AND workflow_key = :wf AND source = 'user'"
            ),
            {"tid": ctx.tenant_id, "pk": USER_PACK_KEY, "wf": workflow_key},
        )
    if result.rowcount == 0:
        raise KeyError(f"user workflow '{workflow_key}' not found")


async def get_full_definition(ctx, workflow_key: str, pack_key: str = USER_PACK_KEY) -> dict:
    """The full stored `WorkflowDefinition` JSON (not just the graph spec) for editing in
    the builder. Resolves DB-first then disk via `load_definition`; raises KeyError if
    neither has it. Includes `source` so the FE can tell user flows from seeded ones."""
    wf = await load_definition(ctx, pack_key, workflow_key)
    data = wf.model_dump(by_alias=True)
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT source FROM workflow_definitions "
                    "WHERE tenant_id = :tid AND pack_key = :pk AND workflow_key = :wf"
                ),
                {"tid": ctx.tenant_id, "pk": pack_key, "wf": workflow_key},
            )
        ).mappings().first()
    data["source"] = row["source"] if row else "seed"
    return data


async def get_run(ctx, run_id) -> dict | None:
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT run_id, pack_key, workflow_key, status, context, current_step, "
                    "created_at, updated_at FROM workflow_runs WHERE run_id = :rid"
                ),
                {"rid": run_id},
            )
        ).mappings().first()
    return dict(row) if row else None


# --------------------------------------------------------------------- execution
def _approver_role(definition: WorkflowDefinition, step_id: str) -> str | None:
    for gate in definition.approvals:
        if gate.step == step_id:
            return gate.approver_persona
    return None


async def _drive(ctx, header, run_id, definition, run_ctx, decisions) -> dict:
    """Run the engine to completion or the next approval; persist state."""
    engine = WorkflowEngine(_handlers(definition.pack, header, decisions))
    try:
        result = await engine.run(definition, ctx=run_ctx)
    except PendingApproval as pa:
        await _persist_steps(ctx, run_id, run_ctx.steps)
        await _save_run(ctx, run_id, "awaiting_approval", run_ctx.as_dict(), pa.step_id)
        summary = (run_ctx.steps.get("summary", {}).get("out", {}) or {}).get("text", "")
        role = _approver_role(definition, pa.step_id)
        await _create_approval(ctx, run_id, pa.step_id, role, summary)
        return {"run_id": run_id, "status": "awaiting_approval", "current_step": pa.step_id}
    await _persist_steps(ctx, run_id, run_ctx.steps)
    await _save_run(ctx, run_id, "completed", result["context"])
    return {"run_id": run_id, "status": "completed", "outputs": result["outputs"]}


async def start_run(ctx, header, run_id, pack_key, workflow_key, inputs) -> dict:
    definition = await load_definition(ctx, pack_key, workflow_key)
    await _create_run(ctx, run_id, pack_key, workflow_key, inputs)
    run_ctx = RunContext(inputs)
    return await _drive(ctx, header, run_id, definition, run_ctx, {})


async def resume_run(ctx, header, run_id, approved, decided_by, comment) -> dict:
    row = await get_run(ctx, run_id)
    if not row:
        raise KeyError("run not found")
    step_id = row["current_step"]
    definition = await load_definition(ctx, row["pack_key"], row["workflow_key"])
    stored = row["context"] if isinstance(row["context"], dict) else json.loads(row["context"])
    run_ctx = RunContext(stored.get("inputs", {}))
    run_ctx.steps = stored.get("steps", {})
    decisions = {step_id: {"approved": approved, "decided_by": decided_by, "comment": comment}}
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                "UPDATE approval_tasks SET status = :st, decision = :d, decided_by = :by, "
                "comment = :c, decided_at = now() WHERE run_id = :rid AND step_id = :sid"
            ),
            {"st": "approved" if approved else "rejected",
             "d": "approved" if approved else "rejected",
             "by": decided_by, "c": comment, "rid": run_id, "sid": step_id},
        )
    if not approved:
        await _save_run(ctx, run_id, "rejected", run_ctx.as_dict(), step_id)
        return {"run_id": run_id, "status": "rejected"}
    return await _drive(ctx, header, run_id, definition, run_ctx, decisions)
