"""Workflow service endpoints. Runs the Temporal worker in-process alongside the API."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import get_engine, new_uuid, tenant_session
from ai_os_shared.errors import NotFoundError, UpstreamError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource
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
