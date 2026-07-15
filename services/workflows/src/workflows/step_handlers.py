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

from ai_os_shared.workflow.engine import RunContext, is_truthy
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
        # Seeded packs reference a prompt file (`prompt`); builder-authored flows (pack
        # 'custom' has no prompts dir on disk) carry the prompt inline via `prompt_text`.
        prompt_text = cfg.get("prompt_text") or (
            load_prompt(cfg["prompt"]) if cfg.get("prompt") else ""
        )
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
        if not cfg.get("files"):
            return {"text": "", "status": "no_files"}
        # SANDBOX: real OCR/extraction (Docling for digital PDFs; a Doc AI for scans) is
        # a MILESTONE_3 add-on. Until then, return a clearly-labeled extracted invoice so
        # the pack flows end to end. Never presented as a real extraction (`_sandbox`).
        return {
            "_sandbox": True,
            "status": "sandbox_extracted",
            "text": "Invoice INV-1042 — Acme Supplies — total 14,280.00 (tax 1,080.00)",
            "vendor_name": "Acme Supplies",
            "invoice_number": "INV-1042",
            "subtotal": 13200.00,
            "tax": 1080.00,
            "total": 14280.00,
            "line_items": [
                {"description": "Widgets", "qty": 100, "unit_price": 132.0, "amount": 13200.0}
            ],
        }

    async def transform(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        # The resolved config IS the transform output (pure data shaping via {{ }}).
        return cfg if isinstance(cfg, dict) else {"value": cfg}

    async def notify(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        return await invoke_connector(
            cfg["connector"], cfg.get("tool", "send"), cfg.get("arguments", {})
        )

    async def approval(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        return await wait_for_approval(step, cfg)

    async def branch(step: Step, cfg: dict, _ctx: RunContext) -> dict:
        # A routing step: evaluate `condition` (already resolved by the engine) and emit
        # the matching flag set. Downstream steps gate on those flags via `when`.
        chosen = cfg.get("on_true") if is_truthy(cfg.get("condition")) else cfg.get("on_false")
        return chosen if isinstance(chosen, dict) else {"value": chosen}

    return {
        StepType.AI_ACTION: ai_action,
        StepType.CONNECTOR_CALL: connector_call,
        StepType.DOCUMENT_RETRIEVE: document_retrieve,
        StepType.DOCUMENT_PARSE: document_parse,
        StepType.TRANSFORM: transform,
        StepType.NOTIFY: notify,
        StepType.APPROVAL: approval,
        StepType.BRANCH: branch,
    }
