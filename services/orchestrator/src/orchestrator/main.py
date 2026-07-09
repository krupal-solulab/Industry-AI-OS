"""Orchestrator endpoints: streaming + non-streaming tenant-scoped chat."""

from __future__ import annotations

import contextlib

import httpx
from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import text
from starlette.responses import StreamingResponse

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.auth import INTERNAL_HEADER
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import admin_session, get_engine, new_uuid, tenant_session
from ai_os_shared.health import HealthRegistry
from ai_os_shared.llm import get_llm
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource
from orchestrator.graph import GRAPH, SYSTEM_PROMPT
from orchestrator.tracing import trace_chat

health = HealthRegistry("orchestrator")


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


@app.post("/chat", tags=["chat"])
async def chat(req: ChatRequest, request: Request) -> dict:
    """Non-streaming chat that runs the LangGraph agent. Traced in Langfuse."""
    ctx = require_context()
    await check_ctx(ctx, "send", Resource(kind="chat", id="chat", tenant_id=ctx.tenant_id))

    model = await _resolve_model(ctx.tenant_id, req.model)
    session_id = await _ensure_session(ctx, req.session_id, model)
    history = await _load_history(ctx, session_id)
    history.append({"role": "user", "content": req.message})

    context = await _retrieve_context(request, req.message) if req.use_rag else ""

    with trace_chat(ctx, model, req.message):
        result = await GRAPH.ainvoke({"messages": history, "context": context, "model": model})
    answer = result["answer"]

    await _persist(ctx, session_id, "user", req.message)
    await _persist(ctx, session_id, "assistant", answer)
    await emit(
        "chat.message",
        resource_kind="chat",
        resource_id=session_id,
        metadata={"model": model, "used_rag": req.use_rag},
    )
    return {"session_id": session_id, "model": model, "answer": answer}


@app.post("/chat/stream", tags=["chat"])
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """Server-sent-events streaming chat (the DoD streamed path)."""
    ctx = require_context()
    await check_ctx(ctx, "send", Resource(kind="chat", id="chat", tenant_id=ctx.tenant_id))

    model = await _resolve_model(ctx.tenant_id, req.model)
    session_id = await _ensure_session(ctx, req.session_id, model)
    history = await _load_history(ctx, session_id)
    context = await _retrieve_context(request, req.message) if req.use_rag else ""

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": f"Relevant context:\n{context}"})
    messages += history + [{"role": "user", "content": req.message}]

    async def generator():
        yield f'data: {{"session_id": "{session_id}", "model": "{model}"}}\n\n'
        collected: list[str] = []
        with trace_chat(ctx, model, req.message):
            async for delta in get_llm().stream_chat(messages, model=model):
                collected.append(delta)
                # Minimal SSE framing; the frontend concatenates deltas.
                safe = delta.replace("\n", "\\n").replace('"', '\\"')
                yield f'data: {{"delta": "{safe}"}}\n\n'
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
