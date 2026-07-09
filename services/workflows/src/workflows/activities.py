"""Temporal activities — the side-effecting steps (DB, LLM, audit).

Activities run outside the deterministic workflow sandbox, so they may do arbitrary
IO. Each rebuilds a TenantContext from the tenant id passed in and uses the shared
tenant-scoped session, so RLS still applies inside durable execution.
"""

from __future__ import annotations

from sqlalchemy import text
from temporalio import activity

from ai_os_shared.audit import emit
from ai_os_shared.db import tenant_session
from ai_os_shared.llm import get_llm
from ai_os_shared.tenant_context import TenantContext
from workflows.shared_defs import ReviewInput, ReviewResult


def _ctx(tenant_id: str, user_id: str = "system") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id=user_id)


@activity.defn
async def summarize_document(inp: ReviewInput) -> str:
    """Summarize the document via the LLM using its stored chunks."""
    ctx = _ctx(inp.tenant_id, inp.submitted_by)
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text(
                "SELECT content FROM document_chunks WHERE document_id = :doc "
                "ORDER BY chunk_index ASC LIMIT 20"
            ),
            {"doc": inp.document_id},
        )
        body = "\n".join(r.content for r in rows)
    if not body.strip():
        return "No extractable text was found for this document."
    messages = [
        {
            "role": "system",
            "content": "Summarize the document for a human reviewer in 5 bullet points.",
        },
        {"role": "user", "content": body[:12000]},
    ]
    try:
        return await get_llm().chat(messages)
    except Exception as exc:  # LLM unavailable -> deterministic placeholder
        return f"(summary unavailable: {exc})"


@activity.defn
async def set_status(
    tenant_id: str, workflow_id: str, status: str, summary: str | None = None
) -> None:
    ctx = _ctx(tenant_id)
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                "UPDATE workflow_instances SET status = :st, "
                "summary = COALESCE(:summary, summary), updated_at = now() "
                "WHERE workflow_id = :wid"
            ),
            {"st": status, "summary": summary, "wid": workflow_id},
        )


@activity.defn
async def record_decision(inp: ReviewInput, result: ReviewResult) -> None:
    """Persist the final decision and write the audit trail."""
    ctx = _ctx(inp.tenant_id, result.decided_by)
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                "UPDATE workflow_instances SET status = :st, decision = :dec, "
                "decided_by = :by, comment = :comment, summary = :summary, "
                "updated_at = now() WHERE workflow_id = :wid"
            ),
            {
                "st": result.status, "dec": result.status, "by": result.decided_by,
                "comment": result.comment, "summary": result.summary,
                "wid": inp.workflow_id,
            },
        )
    await emit(
        f"workflow.{result.status}",
        resource_kind="workflow",
        resource_id=inp.workflow_id,
        after={
            "decision": result.status,
            "decided_by": result.decided_by,
            "comment": result.comment,
            "document_id": inp.document_id,
        },
        ctx=ctx,
    )
