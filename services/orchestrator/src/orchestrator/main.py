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
    IntentResult,
    Mode,
    build_system_prompt,
    classify_intent,
    find_action,
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


async def _workflow_definitions(request: Request) -> list[dict]:
    """Fetch this tenant's workflow definitions (seed + user-built) from the workflows
    service (`GET /packs/definitions`), forwarding the signed context header so tenancy is
    preserved. Returns [] on ANY failure — a definitions-lookup error must never break chat;
    the assistant then falls back to config-only workflow keys + the default pack."""
    settings = get_settings()
    header = request.headers.get(INTERNAL_HEADER)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.workflows_url.rstrip('/')}/packs/definitions",
                headers={INTERNAL_HEADER: header} if header else {},
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("chat.definitions_fetch_failed", error=str(exc))
        return []


def _workflow_pack_map(definitions: list[dict]) -> tuple[list[str], dict[str, str]]:
    """From `/packs/definitions` specs build (a) the ordered, de-duped list of workflow
    keys to hand the classifier as `extra_workflow_keys`, and (b) a `{workflow_key:
    pack_key}` map so a resolved key is started with the CORRECT pack (user flows →
    'custom'). All definitions are included — this also lets seeded flows resolve their
    real pack instead of the workspace default."""
    keys: list[str] = []
    pack_by_key: dict[str, str] = {}
    for d in definitions:
        wf = d.get("workflow_key")
        pack = d.get("pack_key")
        if not wf:
            continue
        if wf not in pack_by_key:
            keys.append(wf)
        if pack:
            pack_by_key[wf] = pack
    return keys, pack_by_key


async def _invoke_connector(request: Request, connector: str, method: str, endpoint: str) -> dict:
    """Run a connector quick-action through the Connector Hub (real data; sandbox if not
    live). Returns an error dict on failure — never fabricated."""
    settings = get_settings()
    header = request.headers.get(INTERNAL_HEADER)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{settings.connectors_url.rstrip('/')}/connectors/{connector}/invoke",
                headers={INTERNAL_HEADER: header} if header else {},
                json={"tool": method, "arguments": {"endpoint": endpoint}},
            )
            resp.raise_for_status()
            return resp.json().get("result", {})
    except httpx.HTTPError as exc:
        return {"status": "error", "connector": connector, "error": str(exc)}


async def _start_pack_workflow(request: Request, pack: str, workflow: str, inputs: dict) -> dict:
    """Ask the workflows service to actually START a pack run. Returns the run result
    (run_id + status) or an error dict."""
    settings = get_settings()
    header = request.headers.get(INTERNAL_HEADER)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.workflows_url.rstrip('/')}/packs/{workflow}/run",
                headers={INTERNAL_HEADER: header} if header else {},
                json={"pack_key": pack, "inputs": inputs},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        return {"status": "error", "error": str(exc)}


async def _gather_backend_data(
    request: Request,
    ir: IntentResult,
    ws,
    use_rag: bool,
    query: str,
    pack_by_key: dict[str, str] | None = None,
) -> str | None:
    """Fetch/act on REAL data for the detected intent and return a context block for the
    model. Never fabricated — on failure we say so. Connector calls + workflow starts go
    through the existing Connector Hub / workflow service APIs (the assistant requests and
    reports; it doesn't reimplement them)."""
    intent = ir.intent

    if intent is Intent.CONNECTOR_ACTION:
        action = find_action(ws.key if ws else None, ir.action)
        if not action:
            return (
                "The user asked for a connector task, but it didn't match a known quick-"
                "action for this workspace. Ask them to connect the relevant tool or clarify."
            )
        result = await _invoke_connector(request, action.connector, action.method, action.endpoint)
        return (
            f"Connector quick-action '{action.label}' via {action.connector} — REAL result "
            f"(present it to the user; if it's a sandbox/error payload, say so honestly):\n"
            f"{json.dumps(result, indent=2)[:2500]}"
        )

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

    if intent is Intent.WORKFLOW_EXECUTION:
        available = (ws.workspace.copilots or ws.workflow_packs) if ws else []
        # Start the run only when a known workflow is identified and we have a pack.
        wf = ir.workflow
        # Prefer the pack from the tenant's definitions (user-built flows → "custom",
        # seeded flows → their real pack); fall back to the workspace default only when
        # the resolved key isn't in the definitions map.
        default_pack = ws.workflow_packs[0] if (ws and ws.workflow_packs) else None
        pack = (pack_by_key or {}).get(wf) if wf else None
        pack = pack or default_pack
        if wf and pack:
            inputs = {
                "invoice_email": {"id": "chat-demo", "from": "vendor@example.com"},
                "has_accounting_connector": False,
                "sheet_id": "demo",
            }
            result = await _start_pack_workflow(request, pack, wf, inputs)
            return (
                f"Workflow '{wf}' was STARTED via the workflow service — REAL result "
                f"(report the run_id + status honestly; if it's awaiting_approval, tell the "
                f"user it's paused for their approval; if it's an error, say so):\n"
                f"{json.dumps(result, indent=2)}"
            )
        return (
            "The user wants to run a workflow but no specific known workflow was identified. "
            f"Workflows this workspace can run: {available or 'none configured'}. Ask which "
            "one, and what inputs (e.g. the invoice/email) — do NOT claim anything ran."
        )

    if intent is Intent.DOCUMENT_ANALYSIS:
        return (
            "The user wants a document analyzed. Ask them to upload it (Documents page) or "
            "identify which stored document; do not invent its contents."
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

    # Fetch the tenant's workflow definitions (seed + user-built) so the classifier can
    # resolve user-authored flow keys and each resolved key is started with its correct
    # pack_key. Fails soft — an empty list keeps config-only keys + the default pack.
    definitions = await _workflow_definitions(request)
    extra_workflow_keys, pack_by_key = _workflow_pack_map(definitions)

    # Intent detection, then gather real backend data for that intent.
    intent_res = await classify_intent(
        req.message, history, ws, model, extra_workflow_keys=extra_workflow_keys
    )
    data_block = await _gather_backend_data(
        request, intent_res, ws, req.use_rag, req.message, pack_by_key
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
