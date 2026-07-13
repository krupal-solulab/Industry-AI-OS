"""Orchestrator endpoints: streaming + non-streaming tenant-scoped chat."""

from __future__ import annotations

import contextlib
import json

import httpx
import structlog
from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import text
from starlette.responses import StreamingResponse

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.auth import INTERNAL_HEADER
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import admin_session, get_engine, new_uuid, tenant_session
from ai_os_shared.errors import UpstreamError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.llm import get_llm
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource
from orchestrator.assistant import (
    Intent,
    Mode,
    build_system_prompt,
    classify_intent,
    last_assistant_had_reminder,
    resolve_workspace,
    workspace_reminder,
)
from orchestrator.tracing import trace_chat

log = structlog.get_logger("aios.orchestrator")
health = HealthRegistry("orchestrator")

# User-facing message when the LLM call fails (bad/missing provider key, litellm down,
# provider quota, timeout). We never leak the raw provider/proxy error to the client.
LLM_UNAVAILABLE = (
    "The assistant is temporarily unavailable — the language model could not be reached. "
    "If this keeps happening, an administrator may need to configure a valid LLM API key."
)


async def _litellm_check() -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=3) as client:
        resp = await client.get(f"{settings.litellm_url.rstrip('/')}/health/liveliness")
        resp.raise_for_status()
    return "ok"


health.register("litellm", _litellm_check)

app = create_app(
    service_name="orchestrator", title="AIOS Orchestrator", health_registry=health
)


@app.on_event("startup")
async def _startup() -> None:
    get_engine()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    use_rag: bool = False
    model: str | None = None  # explicit override; else per-tenant, else default
    # The active industry workspace (the FE is industry-specific so it knows this).
    # If omitted, the assistant falls back to the user's profile `login_source`.
    workspace: str | None = None


async def _resolve_model(tenant_id: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    async with admin_session() as s:
        # ctx.tenant_id is the Keycloak Organization id; the control-plane table
        # keys tenants on keycloak_org_id (its uuid PK is internal).
        row = (
            await s.execute(
                text(
                    "SELECT settings->>'chat_model' AS m FROM tenants "
                    "WHERE keycloak_org_id = :id OR id::text = :id OR slug = :id"
                ),
                {"id": tenant_id},
            )
        ).first()
    return (row.m if row and row.m else None) or get_settings().default_chat_model


async def _retrieve_context(request: Request, query: str) -> str:
    """Ask the knowledge service for RAG context, forwarding the signed context
    header so tenant scoping is preserved across the hop."""
    settings = get_settings()
    header = request.headers.get(INTERNAL_HEADER)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.knowledge_url.rstrip('/')}/retrieve",
                headers={INTERNAL_HEADER: header} if header else {},
                json={"query": query, "top_k": 4},
            )
            resp.raise_for_status()
            chunks = resp.json().get("results", [])
    except httpx.HTTPError:
        return ""
    return "\n\n".join(c["content"] for c in chunks)


async def _load_history(ctx, session_id: str) -> list[dict]:
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text(
                "SELECT role, content FROM chat_messages WHERE session_id = :sid "
                "ORDER BY created_at ASC"
            ),
            {"sid": session_id},
        )
        return [{"role": r.role, "content": r.content} for r in rows]


async def _ensure_session(ctx, session_id: str | None, model: str) -> str:
    async with tenant_session(ctx) as s:
        if session_id:
            exists = (
                await s.execute(
                    text("SELECT 1 FROM chat_sessions WHERE id = :id"), {"id": session_id}
                )
            ).first()
            if exists:
                return session_id
        new_id = session_id or new_uuid()
        await s.execute(
            text(
                "INSERT INTO chat_sessions (id, tenant_id, user_id, model) "
                "VALUES (:id, :tid, :uid, :model)"
            ),
            {"id": new_id, "tid": ctx.tenant_id, "uid": ctx.user_id, "model": model},
        )
        return new_id


async def _persist(ctx, session_id: str, role: str, content: str) -> None:
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                "INSERT INTO chat_messages (tenant_id, session_id, role, content) "
                "VALUES (:tid, :sid, :role, :content)"
            ),
            {"tid": ctx.tenant_id, "sid": session_id, "role": role, "content": content},
        )


async def _login_source(ctx) -> str | None:
    """The user's industry, from their profile — used to resolve the active workspace
    when the request doesn't carry one explicitly."""
    try:
        async with tenant_session(ctx) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT login_source FROM user_profiles "
                        "WHERE keycloak_user_id = :uid OR email = :email"
                    ),
                    {"uid": ctx.user_id, "email": ctx.email or ""},
                )
            ).first()
        return row.login_source if row else None
    except Exception:
        return None


async def _workflows(request: Request, path: str = "") -> list | dict | None:
    """Read real workflow state from the workflows service (forwarding the signed
    context header so tenancy is preserved). Returns None if the call fails — the
    assistant then tells the user status is unavailable rather than inventing it."""
    settings = get_settings()
    header = request.headers.get(INTERNAL_HEADER)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.workflows_url.rstrip('/')}/workflows{path}",
                headers={INTERNAL_HEADER: header} if header else {},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


async def _gather_backend_data(
    request: Request, intent: Intent, ws, use_rag: bool, query: str
) -> str | None:
    """Fetch REAL data relevant to the detected intent and return it as a context block
    for the model. Never fabricated — on failure we say so. Nothing here executes a
    workflow or calls a connector; that stays in the orchestrator/workflow services."""
    if intent is Intent.KNOWLEDGE_SEARCH or use_rag:
        ctx_text = await _retrieve_context(request, query)
        return (
            f"Evidence retrieved from the tenant's knowledge base:\n{ctx_text}"
            if ctx_text
            else "Knowledge base returned no matching passages for this query."
        )

    if intent in (Intent.WORKFLOW_STATUS, Intent.APPROVAL_STATUS):
        rows = await _workflows(request)
        if rows is None:
            return (
                "Workflow status is currently unavailable "
                "(the workflow service did not respond)."
            )
        if intent is Intent.APPROVAL_STATUS:
            pending = ("await", "pending", "review")
            rows = [
                r for r in rows
                if not r.get("decision")
                and any(k in str(r.get("status", "")).lower() for k in pending)
            ]
        label = (
            "Items awaiting approval"
            if intent is Intent.APPROVAL_STATUS
            else "Recent workflows"
        )
        body = json.dumps(rows[:10], indent=2)
        return f"{label} for this tenant (real data, most recent first):\n{body}"

    if intent in (Intent.WORKFLOW_EXECUTION, Intent.DOCUMENT_ANALYSIS):
        available = (ws.workspace.copilots or ws.workflow_packs) if ws else []
        rows = await _workflows(request)
        recent = json.dumps(rows[:5], indent=2) if rows else "none / unavailable"
        return (
            "WORKFLOW REQUEST HANDLING — do NOT claim anything ran.\n"
            f"Workflows this workspace can automate: {available or 'none configured'}.\n"
            f"The tenant's recent workflow runs (real): {recent}.\n"
            "Execution capability today: only 'document_review_approval' runs end-to-end "
            "(needs an uploaded document). For any other workflow, identify it, list the "
            "inputs you'd need, and offer to start it once those are provided — but state "
            "plainly that automated execution for it is not wired yet. Never invent a run id "
            "or a result."
        )
    return None


@app.post("/chat", tags=["chat"])
async def chat(req: ChatRequest, request: Request) -> dict:
    """Workspace-aware assistant chat.

    Flow: resolve the active workspace → detect intent → pull REAL backend data for that
    intent (knowledge / workflow status) via existing service APIs → answer with a
    workspace-aware system prompt → apply the Mode-2 reminder policy. The assistant never
    executes workflows or invents data; it requests and reports on the other services.
    """
    ctx = require_context()
    await check_ctx(ctx, "send", Resource(kind="chat", id="chat", tenant_id=ctx.tenant_id))

    model = await _resolve_model(ctx.tenant_id, req.model)
    session_id = await _ensure_session(ctx, req.session_id, model)
    history = await _load_history(ctx, session_id)

    # Workspace awareness + mode.
    ws = resolve_workspace(req.workspace, await _login_source(ctx))
    mode = Mode.parse(get_settings().assistant_mode)

    # Intent detection, then gather real backend data for that intent.
    intent_res = await classify_intent(req.message, history, ws, model)
    data_block = await _gather_backend_data(
        request, intent_res.intent, ws, req.use_rag, req.message
    )

    messages: list[dict] = [{"role": "system", "content": build_system_prompt(ws, mode)}]
    if data_block:
        messages.append({"role": "system", "content": data_block})
    messages += history + [{"role": "user", "content": req.message}]

    try:
        with trace_chat(ctx, model, req.message):
            answer = await get_llm().chat(messages, model=model)
    except Exception as exc:
        # Persist the user turn so history stays consistent, then surface a clean error.
        with contextlib.suppress(Exception):
            await _persist(ctx, session_id, "user", req.message)
        log.error("chat.llm_failed", error=str(exc), model=model)
        raise UpstreamError(LLM_UNAVAILABLE) from exc

    # Mode-2 reminder: only for questions unrelated to the workspace (general chat),
    # only when a workspace is active, and never twice in a row.
    if (
        mode is Mode.STRICT_LENIENT
        and ws is not None
        and intent_res.intent in (Intent.GENERAL_QUESTION, Intent.GENERAL_CONVERSATION)
        and not last_assistant_had_reminder(history)
    ):
        answer = f"{answer.rstrip()}\n\n{workspace_reminder(ws)}"

    await _persist(ctx, session_id, "user", req.message)
    await _persist(ctx, session_id, "assistant", answer)
    await emit(
        "chat.message",
        resource_kind="chat",
        resource_id=session_id,
        metadata={
            "model": model,
            "used_rag": req.use_rag,
            "intent": intent_res.intent.value,
            "workspace": ws.key if ws else None,
            "mode": mode.value,
        },
    )
    return {
        "session_id": session_id,
        "model": model,
        "answer": answer,
        "intent": intent_res.intent.value,
        "workspace": ws.key if ws else None,
    }


@app.post("/chat/stream", tags=["chat"])
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """Server-sent-events streaming chat (the DoD streamed path)."""
    ctx = require_context()
    await check_ctx(ctx, "send", Resource(kind="chat", id="chat", tenant_id=ctx.tenant_id))

    model = await _resolve_model(ctx.tenant_id, req.model)
    session_id = await _ensure_session(ctx, req.session_id, model)
    history = await _load_history(ctx, session_id)
    context = await _retrieve_context(request, req.message) if req.use_rag else ""

    ws = resolve_workspace(req.workspace, await _login_source(ctx))
    mode = Mode.parse(get_settings().assistant_mode)
    remind = (
        mode is Mode.STRICT_LENIENT
        and ws is not None
        and not last_assistant_had_reminder(history)
    )
    reminder = workspace_reminder(ws) if remind else ""

    messages: list[dict] = [{"role": "system", "content": build_system_prompt(ws, mode)}]
    if context:
        messages.append({"role": "system", "content": f"Relevant context:\n{context}"})
    messages += history + [{"role": "user", "content": req.message}]

    async def generator():
        # json.dumps handles all SSE-unsafe characters (quotes, newlines, unicode).
        yield f"data: {json.dumps({'session_id': session_id, 'model': model})}\n\n"
        collected: list[str] = []
        errored = False
        try:
            with trace_chat(ctx, model, req.message):
                async for delta in get_llm().stream_chat(messages, model=model):
                    collected.append(delta)
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
        except Exception as exc:
            # Don't break the stream — tell the client explicitly so it shows a real
            # error instead of silently rendering nothing.
            errored = True
            log.error("chat_stream.llm_failed", error=str(exc), model=model)
            yield f"data: {json.dumps({'error': LLM_UNAVAILABLE})}\n\n"

        if errored:
            # Keep the user turn; don't persist an empty/partial assistant message.
            with contextlib.suppress(Exception):
                await _persist(ctx, session_id, "user", req.message)
            yield "data: [DONE]\n\n"
            return

        if reminder:
            tail = f"\n\n{reminder}"
            collected.append(tail)
            yield f"data: {json.dumps({'delta': tail})}\n\n"
        answer = "".join(collected)
        await _persist(ctx, session_id, "user", req.message)
        await _persist(ctx, session_id, "assistant", answer)
        with contextlib.suppress(Exception):
            await emit(
                "chat.message",
                resource_kind="chat",
                resource_id=session_id,
                metadata={"model": model, "streamed": True},
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")
