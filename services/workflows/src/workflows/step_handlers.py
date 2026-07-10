"""Step handlers — the bridge between the generic engine and the platform's services.

Each handler executes one step *type* by calling a Milestone-1 capability:
  ai.action        → the LLM (LiteLLM) with a pack prompt template
  connector.call   → the Connector Hub (Nango / Composio) — THIS is where the
                     construction connectors plug in once accounts/creds exist
  document.retrieve→ the Knowledge service (pgvector RAG)
  document.parse   → the Knowledge service (Docling/OCR) — placeholder until wired
  approval         → a human decision (awaited via the injected `wait_for_approval`,
                     which is a Temporal signal in production, or a fake in tests)
  transform/notify → in-engine / Connector Hub

Everything is injected via `build_handlers`, so the engine stays pure and each
dependency is swappable and unit-testable. Connectors that aren't configured yet
return `{"status": "not_configured", ...}` rather than failing the run.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from ai_os_shared.workflow.engine import RunContext
from ai_os_shared.workflow.schema import Step, StepType

# Dependency callable signatures (implemented by the workflows service at runtime).
LLMChat = Callable[[list[dict], str | None], Awaitable[str]]
ConnectorInvoke = Callable[[str, str, dict], Awaitable[dict]]
PromptLoader = Callable[[str], str]
Retrieve = Callable[[str, int], Awaitable[dict]]
ApprovalWaiter = Callable[[Step, dict], Awaitable[dict]]


def build_handlers(
    *,
    llm_chat: LLMChat,
    invoke_connector: ConnectorInvoke,
    load_prompt: PromptLoader,
    retrieve: Retrieve,
    wait_for_approval: ApprovalWaiter,
) -> dict[StepType, Callable]:
    async def ai_action(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        prompt_text = load_prompt(cfg["prompt"])
        payload = cfg.get("context", cfg.get("input", ""))
        user = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        answer = await llm_chat(
            [{"role": "system", "content": prompt_text}, {"role": "user", "content": user}],
            cfg.get("model"),
        )
        if cfg.get("output") == "json":
            try:
                parsed = json.loads(answer)
                if isinstance(parsed, dict):
                    parsed.setdefault("text", answer)
                    return parsed
            except ValueError:
                pass
        return {"text": answer}

    async def connector_call(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        return await invoke_connector(
            cfg["connector"], cfg.get("tool", ""), cfg.get("arguments", {})
        )

    async def document_retrieve(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        return await retrieve(cfg.get("query", ""), int(cfg.get("top_k", 5)))

    async def document_parse(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        # Standalone parse/OCR of arbitrary files needs a Knowledge endpoint that does
        # not exist yet; if raw text is supplied, pass it through.
        if "text" in cfg:
            return {"text": cfg["text"]}
        return {"text": "", "status": "parse_pending", "files": cfg.get("files")}

    async def transform(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        # The resolved config IS the transform output (pure data shaping via {{ }}).
        return cfg if isinstance(cfg, dict) else {"value": cfg}

    async def notify(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        return await invoke_connector(
            cfg["connector"], cfg.get("tool", "send"), cfg.get("arguments", {})
        )

    async def approval(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        return await wait_for_approval(step, cfg)

    return {
        StepType.AI_ACTION: ai_action,
        StepType.CONNECTOR_CALL: connector_call,
        StepType.DOCUMENT_RETRIEVE: document_retrieve,
        StepType.DOCUMENT_PARSE: document_parse,
        StepType.TRANSFORM: transform,
        StepType.NOTIFY: notify,
        StepType.APPROVAL: approval,
    }
