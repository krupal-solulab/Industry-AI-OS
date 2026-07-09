# ADR-0015: Workflow Pack Framework — declarative definitions over a generic engine

- **Status:** Accepted
- **Date:** 2026-07-09
- **Decision:** **Build** (on reused primitives) — a data-driven workflow engine that
  executes **declarative workflow definitions** (JSON), rather than writing custom code
  per workflow.

## Context

Milestone 1 delivered the reusable platform and **one hardcoded** Temporal workflow
(`DocumentReviewApproval`). The product thesis (validated by the Construction & Legal
POC) is that **80–90% of orchestration is shared across industries** and only the
workflows, personas, business logic, and connectors change. Every POC workflow — RFI,
Change Order, Contract Review, etc. — follows the **same 11-part template** (Business
Goal · Personas · Trigger · Workflow · AI Actions · Human Approval · Connectors ·
Inputs · Outputs · Business Value) and the same shape: trigger → steps → AI actions →
human approval → connector actions.

If we implement each workflow as bespoke code, we hardcode industry logic into the
platform and re-pay the integration cost per workflow. That contradicts the whole
architecture.

## Options considered

1. **Per-workflow code** — each workflow is a hand-written Temporal workflow. Fast for
   the first one; O(n) engineering per workflow; industry logic leaks into the core.
2. **Data-driven engine (chosen)** — workflows are validated JSON definitions; a single
   generic Temporal interpreter (`PackWorkflow`) executes them step by step against a
   closed vocabulary of step types, calling existing services (orchestrator, knowledge,
   connectors) as activities.
3. **Third-party low-code engine** (n8n, Zapier, Temporal-only DSL) — either SaaS
   lock-in / weak on-prem story, or not multi-tenant + RBAC + audit aware in the way
   the platform requires.

## Decision

Build the **Workflow Pack Framework**: a definition schema + registry + generic Temporal
executor + step engine + AI-action engine + approval engine, all layered on Milestone 1
services. Industries ship as **packs** = a folder of JSON definitions + prompt templates
+ persona/RBAC config + connector requirements. Adding an industry changes no platform code.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Thin build over bought primitives (Temporal, LiteLLM, Connector Hub); no new engine reinvented. |
| Complexity | Moderate: a definition interpreter + closed step vocabulary. Bounded and testable. |
| Effort | Higher once (the engine); near-zero per subsequent workflow/industry (JSON + prompts). |
| Scalability | High: reuses Temporal's durable execution; new industries add data, not load-bearing code. |
| Lock-in risk | Low: definitions are our own portable JSON; steps map to swappable internal services. |
| Cost | Low incremental: no new infra beyond M1; packs are content. |
| Community maturity | Engine is custom, but every dependency it orchestrates is mature (Temporal, etc.). |

## Consequences

- The M1 `DocumentReviewApproval` workflow is **generalized** into one `PackWorkflow`
  interpreter; the approval mechanics are reused as the generic Approval Engine.
- Steps use a **closed vocabulary** (`connector.call`, `document.parse`,
  `document.retrieve`, `ai.action`, `approval`, `transform`, `notify`, `branch`) — no
  arbitrary code execution, so definitions are safe, reviewable, and sandboxed.
- Inputs flow between steps via a **restricted expression resolver** (JSONPath-style over
  a per-run context), never `eval`.
- **Packs are authored as versioned JSON files in the repo** and seeded into a per-tenant
  **DB registry** that controls enable/version — code-reviewed definitions + runtime control.
- Personas map to platform roles → Cerbos, so approval routing (e.g., "PM approval") is
  RBAC-driven and industry-neutral in the engine.
- Trade-off accepted: a definition interpreter is more upfront work than one hardcoded
  flow, but it is the only design consistent with the 80–90%-reuse goal. Superseding the
  M1 single-workflow approach is intentional.
- The framework carries **no industry logic**; Construction (ADR-to-come, M3) and Legal
  (M4) are packs on top.
