# ADR-0017: AI Assistant — workspace-aware conversational layer

- **Status:** Accepted
- **Date:** 2026-07-13
- **Decision:** The chat endpoint is a **workspace-aware AI Assistant** that owns
  conversation, intent detection, and context — and **delegates** all execution to the
  existing orchestrator/workflow/connector services. It never executes workflows,
  invokes connectors, or fabricates data itself.

## Context

Each industry frontend needs a conversational interface scoped to its workspace
(ADR-0016) — not a generic chatbot. It must know the active workspace, detect what the
user wants (question, knowledge search, run a workflow, check status/approval, …), answer
using the configured LLM, and surface real backend state. Crucially, we must **not**
duplicate orchestration logic in the assistant: planning, tool selection, workflow
execution, connector calls, and approval orchestration already live in the workflow/
orchestrator services.

## Options considered

1. **Generic chatbot** — a plain LLM chat with no workspace awareness or intent routing.
   Rejected: doesn't meet the per-industry product requirement.
2. **Assistant that also executes** (calls Temporal / connectors directly) — rejected:
   duplicates orchestration, blurs the trust/execution boundary, risks fabricated results.
3. **Thin conversational layer over existing services (chosen)** — the assistant resolves
   the workspace, classifies intent, fetches real data from existing APIs, phrases the
   answer, and applies a configurable workspace-reminder policy. Execution requests go to
   the workflow service; the assistant only reports on them.

## Decision

Implement the assistant in the orchestrator `/chat` (+ `/chat/stream`):

- **Workspace awareness** — from the request `workspace` (FE knows it) or the user's
  `login_source`, resolved via the ADR-0016 industry registry.
- **Intent detection** — LLM→JSON classifier over a closed set (general / workspace /
  knowledge-search / workflow-execution / document-analysis / workflow-status /
  approval-status / conversation); fails safe to a general answer.
- **Real data only** — knowledge search → knowledge `/retrieve`; status/approvals →
  workflow service; workflow execution → identify + collect inputs and state honestly
  what is executable today. No fabricated data, connector responses, or run results.
- **Configurable mode** (`ASSISTANT_MODE` env, one setting, no logic branching to switch):
  `strict` (reject unrelated) | `strict_lenient` (default — answer, append a workspace
  reminder for unrelated questions, never twice in a row) | `lenient` (plain assistant).
- **Failure handling** — a missing/invalid LLM key or provider error yields a clean error
  (non-stream `502`; stream: an `{error}` SSE frame), never a fake answer.

## Consequences

- Clear separation of duties: **Assistant** = conversation, intent, context, interaction;
  **Orchestrator/Workflow/Connector services** = planning, execution, connectors,
  approvals. The assistant calls them; it does not reimplement them.
- Response shaping guidance (Summary / Evidence / Confidence / Next Action / Workflow
  Status) is prompt-driven, applied when it fits.
- Deeper context (current project / uploaded doc / current workflow) is not yet persisted
  beyond chat history — workspace is per-request and "current workflow" is read live.
  A session-context store is a later increment.
- Depends on ADR-0005 (LiteLLM) for model access and ADR-0016 for workspace config.
