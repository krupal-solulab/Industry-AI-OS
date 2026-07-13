"""AI Assistant behavior — the conversational layer of a per-industry workspace.

Responsibilities kept HERE (conversation concerns only):
  - workspace awareness      (which industry workspace is active)
  - intent detection         (classify each message)
  - mode / reminder policy    (Strict | Strict+Lenient(default) | Lenient)
  - system-prompt + response-format shaping

Responsibilities that are NOT here (they belong to the orchestrator/workflows +
connector services and are only *called*, never reimplemented):
  - planning, tool selection, workflow execution, connector invocation, approval
    orchestration.

This module never fabricates business data, connector responses, or workflow
outcomes. It classifies + phrases; the real work is done by existing backend APIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from ai_os_shared.industry import Industry, get_industry
from ai_os_shared.llm import get_llm


class Mode(str, Enum):
    STRICT = "strict"  # reject anything unrelated to the workspace
    STRICT_LENIENT = "strict_lenient"  # answer, but remind (DEFAULT)
    LENIENT = "lenient"  # normal assistant, no reminder

    @classmethod
    def parse(cls, raw: str | None) -> Mode:
        try:
            return cls(str(raw or "").strip().lower())
        except ValueError:
            return cls.STRICT_LENIENT


class Intent(str, Enum):
    GENERAL_QUESTION = "general_question"
    WORKSPACE_QUESTION = "workspace_question"
    KNOWLEDGE_SEARCH = "knowledge_search"
    WORKFLOW_EXECUTION = "workflow_execution"
    DOCUMENT_ANALYSIS = "document_analysis"
    WORKFLOW_STATUS = "workflow_status"
    APPROVAL_STATUS = "approval_status"
    GENERAL_CONVERSATION = "general_conversation"


@dataclass
class IntentResult:
    intent: Intent
    workflow: str | None = None  # a workspace workflow/copilot key, if one was named


# A stable lead-in so we can detect whether the previous assistant turn already
# carried the workspace reminder (spec: don't repeat it back-to-back).
REMINDER_LEAD = "You're currently working inside the"


def resolve_workspace(explicit: str | None, login_source: str | None) -> Industry | None:
    """The active workspace = the FE-provided industry key if present, else the user's
    `login_source`. Returns None when neither resolves to a configured industry (the
    assistant then behaves generically)."""
    return get_industry(explicit) or get_industry(login_source)


def _humanize(key: str) -> str:
    return key.replace("_", " ").strip()


def _capabilities(ws: Industry, limit: int = 5) -> str:
    """A short, human phrase of what this workspace can automate — from the pack's
    copilots (fallback to its workflow packs). Real config, not invented."""
    items = ws.workspace.copilots or ws.workflow_packs
    human = [_humanize(k) for k in items][:limit]
    if not human:
        return "industry-specific workflows and document analysis"
    if len(human) == 1:
        return human[0]
    return ", ".join(human[:-1]) + " and " + human[-1]


def workspace_reminder(ws: Industry) -> str:
    return (
        f"{REMINDER_LEAD} {ws.name}. I can also help automate "
        f"{_capabilities(ws)}."
    )


def last_assistant_had_reminder(history: list[dict]) -> bool:
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            return REMINDER_LEAD in (msg.get("content") or "")
    return False


def build_system_prompt(ws: Industry | None, mode: Mode) -> str:
    """Workspace-aware system prompt. The mode only changes the unrelated-question
    policy line — the rest is constant, so switching modes needs no logic change."""
    base = (
        "You are the AI Assistant for the Industry AI OS — the conversational interface "
        "for the user's active industry workspace. You are tenant-scoped: only use "
        "information available within the current tenant's context.\n\n"
        "GROUND RULES (non-negotiable):\n"
        "- Never fabricate business data, connector responses, or workflow results.\n"
        "- Never claim a workflow ran or completed unless you are given its real status.\n"
        "- If backend data is unavailable, say so plainly.\n"
        "- You handle conversation, intent, and context. Planning, workflow execution, "
        "connector calls and approvals are done by the orchestrator/workflow services — "
        "you request and report on them, you do not perform them.\n\n"
        "RESPONSE FORMAT — when it fits the question, structure the answer as:\n"
        "Summary; Evidence (only if you were given real evidence); Confidence; "
        "Recommended Next Action; Workflow Status (only if applicable). "
        "For simple chit-chat, just reply naturally.\n"
    )
    if ws is not None:
        terms = ", ".join(f"'{k}'→'{v}'" for k, v in ws.workspace.terminology.items())
        base += (
            f"\nACTIVE WORKSPACE: {ws.name} (industry '{ws.key}'). "
            f"It can automate: {_capabilities(ws, limit=12)}. "
        )
        if terms:
            base += f"Use this workspace's terminology where natural ({terms}). "

    if mode is Mode.STRICT and ws is not None:
        base += (
            f"\nMODE=STRICT: Only answer questions related to the {ws.name}. If a question "
            "is unrelated, politely decline and steer back to the workspace's capabilities."
        )
    elif mode is Mode.STRICT_LENIENT:
        base += (
            "\nMODE=STRICT+LENIENT: Answer every question normally, even if unrelated to "
            "the workspace. Do NOT refuse. A short workspace reminder is appended "
            "separately — do not add your own."
        )
    else:  # LENIENT
        base += "\nMODE=LENIENT: Behave like a normal assistant. No workspace reminder."
    return base


async def classify_intent(
    message: str, history: list[dict], ws: Industry | None, model: str | None
) -> IntentResult:
    """LLM intent classification returning strict JSON. On any parsing/LLM failure we
    fall back to GENERAL_QUESTION so chat never hard-fails on classification."""
    workflow_keys = (ws.workspace.copilots or ws.workflow_packs) if ws else []
    recent = history[-6:]
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
    sys = (
        "You are an intent classifier for an industry-workspace AI assistant. "
        "Classify the user's LATEST message into exactly one intent and, if they refer "
        "to a specific workspace workflow, its key.\n"
        f"Intents: {', '.join(i.value for i in Intent)}.\n"
        f"Known workflow keys for this workspace: {workflow_keys or 'none'}.\n"
        "Guidance: 'run/verify/create/generate/review <thing>' => workflow_execution; "
        "'what's the status of / did it finish' => workflow_status; "
        "'is it approved / pending approval' => approval_status; "
        "'find / search / what do our docs say' => knowledge_search; "
        "'analyze/summarize this document/file' => document_analysis; "
        "questions about what this workspace does => workspace_question; "
        "greetings/small talk => general_conversation; anything else => general_question.\n"
        'Respond with ONLY compact JSON: {"intent": "<intent>", "workflow": "<key or null>"}.'
    )
    prompt = f"Recent conversation:\n{convo}\n\nLATEST user message:\n{message}"
    try:
        raw = await get_llm().chat(
            [{"role": "system", "content": sys}, {"role": "user", "content": prompt}],
            model=model,
            temperature=0,
        )
        data = _extract_json(raw)
        intent = Intent(str(data.get("intent", "")).strip().lower())
        wf = data.get("workflow")
        wf = str(wf).strip() if wf and str(wf).lower() not in {"null", "none", ""} else None
        return IntentResult(intent=intent, workflow=wf)
    except Exception:
        return IntentResult(intent=Intent.GENERAL_QUESTION)


def _extract_json(raw: str) -> dict:
    """Pull the first JSON object out of an LLM reply (tolerates code fences / prose)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no json object in response")
    return json.loads(raw[start : end + 1])
