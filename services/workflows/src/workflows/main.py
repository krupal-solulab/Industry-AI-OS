"""Workflow service endpoints. Runs the Temporal worker in-process alongside the API."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.auth import INTERNAL_HEADER
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import get_engine, new_uuid, tenant_session
from ai_os_shared.errors import NotFoundError, UpstreamError, ValidationError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource
from workflows import pack_runtime
from workflows.shared_defs import Decision, ReviewInput
from workflows.worker import build_worker, get_client
from workflows.workflow import DocumentReviewApproval

log = structlog.get_logger("aios.workflows")
health = HealthRegistry("workflows")
_worker_task: asyncio.Task | None = None


async def _temporal_check() -> str:
    await get_client()
    return "ok"


health.register("temporal", _temporal_check)


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_engine()
    global _worker_task

    async def _run_worker():
        try:
            worker = await build_worker()
            log.info("worker.start", task_queue=get_settings().temporal_task_queue)
            await worker.run()
        except Exception as exc:  # keep the API up even if Temporal is briefly down
            log.error("worker.crashed", error=str(exc))

    _worker_task = asyncio.create_task(_run_worker())
    try:
        yield
    finally:
        if _worker_task:
            _worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _worker_task


app = create_app(
    service_name="workflows",
    title="AIOS Workflow Service",
    health_registry=health,
    lifespan=lifespan,
)


class StartReview(BaseModel):
    document_id: str


class DecisionBody(BaseModel):
    comment: str = ""


@app.post("/workflows/document-review", status_code=201, tags=["workflows"])
async def start_review(body: StartReview) -> dict:
    ctx = require_context()
    await check_ctx(ctx, "start", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))

    workflow_id = f"review-{ctx.tenant_id}-{new_uuid()}"
    inp = ReviewInput(
        tenant_id=ctx.tenant_id,
        document_id=body.document_id,
        workflow_id=workflow_id,
        submitted_by=ctx.user_id,
    )
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO workflow_instances
                   (tenant_id, workflow_id, type, status, document_id, created_by)
                   VALUES (:tid, :wid, 'document_review_approval', 'running', :doc, :by)"""
            ),
            {"tid": ctx.tenant_id, "wid": workflow_id, "doc": body.document_id, "by": ctx.user_id},
        )

    client = await get_client()
    handle = await client.start_workflow(
        DocumentReviewApproval.run,
        inp,
        id=workflow_id,
        task_queue=get_settings().temporal_task_queue,
    )
    async with tenant_session(ctx) as s:
        await s.execute(
            text("UPDATE workflow_instances SET run_id = :rid WHERE workflow_id = :wid"),
            {"rid": handle.result_run_id, "wid": workflow_id},
        )
    await emit(
        "workflow.start",
        resource_kind="workflow",
        resource_id=workflow_id,
        after={"document_id": body.document_id, "type": "document_review_approval"},
    )
    return {"workflow_id": workflow_id, "status": "running"}


async def _signal_decision(workflow_id: str, approved: bool, comment: str) -> None:
    ctx = require_context()
    action = "approve" if approved else "reject"
    await check_ctx(
        ctx, action, Resource(kind="workflow", id=workflow_id, tenant_id=ctx.tenant_id)
    )
    # Ensure the workflow belongs to this tenant (RLS-scoped lookup).
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text("SELECT 1 FROM workflow_instances WHERE workflow_id = :wid"),
                {"wid": workflow_id},
            )
        ).first()
    if not row:
        raise NotFoundError("Workflow not found for this tenant")
    try:
        client = await get_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(
            DocumentReviewApproval.decide,
            Decision(approved=approved, decided_by=ctx.user_id, comment=comment),
        )
    except Exception as exc:
        raise UpstreamError(f"Failed to signal workflow: {exc}") from exc


@app.post("/workflows/{workflow_id}/approve", tags=["workflows"])
async def approve(workflow_id: str, body: DecisionBody) -> dict:
    await _signal_decision(workflow_id, True, body.comment)
    return {"workflow_id": workflow_id, "decision": "approved"}


@app.post("/workflows/{workflow_id}/reject", tags=["workflows"])
async def reject(workflow_id: str, body: DecisionBody) -> dict:
    await _signal_decision(workflow_id, False, body.comment)
    return {"workflow_id": workflow_id, "decision": "rejected"}


@app.get("/workflows", tags=["workflows"])
async def list_workflows(limit: int = 100) -> list[dict]:
    ctx = require_context()
    await check_ctx(ctx, "list", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text(
                "SELECT workflow_id, type, status, document_id, decision, decided_by, "
                "comment, created_at, updated_at FROM workflow_instances "
                "ORDER BY created_at DESC LIMIT :lim"
            ),
            {"lim": limit},
        )
        return [dict(r._mapping) | {
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        } for r in rows]


@app.get("/workflows/{workflow_id}", tags=["workflows"])
async def get_workflow(workflow_id: str) -> dict:
    ctx = require_context()
    await check_ctx(
        ctx, "read", Resource(kind="workflow", id=workflow_id, tenant_id=ctx.tenant_id)
    )
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT workflow_id, type, status, document_id, summary, decision, "
                    "decided_by, comment, created_at, updated_at "
                    "FROM workflow_instances WHERE workflow_id = :wid"
                ),
                {"wid": workflow_id},
            )
        ).first()
    if not row:
        raise NotFoundError("Workflow not found")
    return dict(row._mapping) | {
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


# ---------------------------------------------------------------- Pack workflows
# Generic runtime for the Workflow Pack Framework: run any seeded pack workflow,
# check its run, and approve/reject the human-in-the-loop pause. Reuses the generic
# engine + step handlers (see pack_runtime); the legacy document-review endpoints above
# remain for back-compat.
class RunPack(BaseModel):
    pack_key: str
    inputs: dict = {}


class CreateDefinition(BaseModel):
    # The full workflow definition authored in the visual builder (11-part template).
    # `pack` is forced to the reserved 'custom' key server-side; `key` is required and
    # becomes the workflow_key. Validated against the WorkflowDefinition schema.
    definition: dict


@app.post("/packs/{workflow_key}/run", status_code=201, tags=["workflows"])
async def run_pack_workflow(workflow_key: str, body: RunPack, request: Request) -> dict:
    ctx = require_context()
    await check_ctx(ctx, "start", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))
    run_id = f"run-{ctx.tenant_id}-{new_uuid()}"
    header = request.headers.get(INTERNAL_HEADER)
    try:
        result = await pack_runtime.start_run(
            ctx, header, run_id, body.pack_key, workflow_key, body.inputs
        )
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc
    await emit(
        "workflow.start",
        resource_kind="workflow",
        resource_id=run_id,
        after={"pack": body.pack_key, "workflow": workflow_key},
    )
    return result


@app.get("/packs/definitions", tags=["workflows"])
async def list_definitions() -> list[dict]:
    """Seeded workflow definitions as graph specs (steps + connectors + latest status) —
    powers the FE flow-graph visualization."""
    ctx = require_context()
    await check_ctx(ctx, "list", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))
    return await pack_runtime.list_definitions(ctx)


@app.get("/packs/definitions/{workflow_key}", tags=["workflows"])
async def get_definition(workflow_key: str, pack_key: str = "custom") -> dict:
    """The FULL stored WorkflowDefinition JSON (with per-step config) — powers editing a
    flow in the visual builder, where the graph-spec list (id/type/name only) is not
    enough. Defaults to the reserved 'custom' pack (user flows); pass ?pack_key= to read a
    seeded flow. 404 if the flow doesn't exist for this tenant."""
    ctx = require_context()
    await check_ctx(
        ctx, "read", Resource(kind="workflow", id=workflow_key, tenant_id=ctx.tenant_id)
    )
    try:
        return await pack_runtime.get_full_definition(ctx, workflow_key, pack_key)
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc


@app.post("/packs/definitions", status_code=201, tags=["workflows"])
async def create_definition(body: CreateDefinition) -> dict:
    """Persist a user-authored (visual-builder) flow under the reserved 'custom' pack.
    The definition is validated against the WorkflowDefinition schema; an invalid body
    (bad schema or missing `key`) is a 422. Returns the flow's graph spec."""
    ctx = require_context()
    await check_ctx(ctx, "start", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))
    try:
        spec = await pack_runtime.create_definition(ctx, body.definition)
    except (ValueError, PydanticValidationError) as exc:
        raise ValidationError(f"Invalid workflow definition: {exc}") from exc
    await emit(
        "workflow.define",
        resource_kind="workflow",
        resource_id=spec["workflow_key"],
        after={"pack": spec["pack_key"], "workflow": spec["workflow_key"], "source": "user"},
    )
    return spec


@app.put("/packs/definitions/{workflow_key}", tags=["workflows"])
async def update_definition(workflow_key: str, body: CreateDefinition) -> dict:
    """Replace an existing user-authored flow. 404 if no such user flow; a seed flow
    can never be mutated (it has no source='user' row to match)."""
    ctx = require_context()
    await check_ctx(ctx, "start", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))
    try:
        spec = await pack_runtime.update_definition(ctx, workflow_key, body.definition)
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc
    except (ValueError, PydanticValidationError) as exc:
        raise ValidationError(f"Invalid workflow definition: {exc}") from exc
    await emit(
        "workflow.update",
        resource_kind="workflow",
        resource_id=workflow_key,
        after={"pack": spec["pack_key"], "workflow": workflow_key, "source": "user"},
    )
    return spec


@app.delete("/packs/definitions/{workflow_key}", tags=["workflows"])
async def delete_definition(workflow_key: str) -> dict:
    """Delete a user-authored flow. 404 if no such user flow; seed flows are protected."""
    ctx = require_context()
    await check_ctx(ctx, "start", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))
    try:
        await pack_runtime.delete_definition(ctx, workflow_key)
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc
    await emit("workflow.delete", resource_kind="workflow", resource_id=workflow_key)
    return {"status": "deleted", "workflow_key": workflow_key}


@app.get("/packs/runs/{run_id}", tags=["workflows"])
async def get_pack_run(run_id: str) -> dict:
    ctx = require_context()
    await check_ctx(ctx, "read", Resource(kind="workflow", id=run_id, tenant_id=ctx.tenant_id))
    row = await pack_runtime.get_run(ctx, run_id)
    if not row:
        raise NotFoundError("Run not found")
    row["created_at"] = row["created_at"].isoformat()
    row["updated_at"] = row["updated_at"].isoformat()
    return row


async def _decide_run(run_id: str, request: Request, approved: bool, comment: str) -> dict:
    ctx = require_context()
    action = "approve" if approved else "reject"
    await check_ctx(ctx, action, Resource(kind="workflow", id=run_id, tenant_id=ctx.tenant_id))
    header = request.headers.get(INTERNAL_HEADER)
    try:
        result = await pack_runtime.resume_run(ctx, header, run_id, approved, ctx.user_id, comment)
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc
    await emit(f"workflow.{action}", resource_kind="workflow", resource_id=run_id)
    return result


@app.post("/packs/runs/{run_id}/approve", tags=["workflows"])
async def approve_run(run_id: str, body: DecisionBody, request: Request) -> dict:
    return await _decide_run(run_id, request, True, body.comment)


@app.post("/packs/runs/{run_id}/reject", tags=["workflows"])
async def reject_run(run_id: str, body: DecisionBody, request: Request) -> dict:
    return await _decide_run(run_id, request, False, body.comment)


@app.post("/packs/seed", tags=["workflows"])
async def seed_packs() -> dict:
    """Seed this tenant's pack registry (workflow_packs/workflow_definitions) from the
    repo pack files. Idempotent — safe to call repeatedly."""
    ctx = require_context()
    await check_ctx(ctx, "start", Resource(kind="workflow", id="*", tenant_id=ctx.tenant_id))
    count = await pack_runtime.seed_tenant_packs(ctx)
    return {"status": "seeded", "definitions": count}
