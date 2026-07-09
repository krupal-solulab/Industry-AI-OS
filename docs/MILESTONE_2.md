# Milestone 2 — Workflow Pack Framework

> Status: **Design (for review)**. No code yet. See [ADR-0015](adr/0015-workflow-pack-framework.md).
> Builds directly on the Milestone 1 platform. **Contains zero industry logic** — the
> Construction pack is Milestone 3, Legal is Milestone 4.

## 1. Goal & principle

Turn the platform's single hardcoded workflow into a **data-driven engine** that executes
**declarative workflow definitions**. A workflow (RFI, Change Order, Contract Review…) is a
**JSON file**, not code. One generic interpreter runs any definition.

> **Adding an industry = drop in a pack (JSON + prompts + connector config). No platform change.**

This is the concrete realization of the "80–90% shared" thesis from the Construction & Legal POC.

## 2. Scope

**In scope (M2):**
- Workflow **definition schema** (the POC's 11-part template as validated JSON).
- **Workflow Registry** — load pack files → validate → seed into a per-tenant DB registry.
- **PackWorkflow executor** — one generic Temporal workflow that interprets a definition.
- **Step Engine** — dispatches each step type to an activity; manages run context.
- **AI Action Engine** — prompt template + inputs (+ optional output schema) → structured result.
- **Approval Engine** — persona-routed human approval (generalized from M1).
- **Connector Registry** — declares connector capabilities; makes **Nango & Composio live**
  (M1 shipped placeholders).
- **Persona catalog + persona→role mapping** feeding RBAC/approvals.
- A trivial, industry-neutral **`demo` pack** to prove the engine end-to-end.

**Out of scope (M2):** any Construction/Legal/Healthcare workflow content; production
credentials for real SaaS; branching/parallel steps (phase 2); a workflow-authoring UI.

## 3. What is reused from Milestone 1 (the 80–90%)

| M2 concern | Reuses M1 |
|---|---|
| Durable execution, retries, signals | Temporal `workflows` service |
| `ai.action` steps | Orchestrator + LiteLLM + Langfuse |
| `document.parse` / `document.retrieve` | Knowledge service (Docling, pgvector) |
| `connector.call` steps | Connector Hub (+ Nango/Composio now live) |
| `approval` steps | `DocumentReviewApproval` mechanics, generalized |
| Approver routing | Cerbos + RBAC roles |
| Run/registry persistence, isolation | Postgres + RLS, tenant context |
| External entry | API Gateway |

**Change to M1:** the hardcoded `DocumentReviewApproval` Temporal workflow is superseded by
the generic `PackWorkflow` interpreter (its approval logic becomes the Approval Engine).

## 4. Workflow definition schema (11-part template → JSON)

```jsonc
{
  "$schema": "aios.workflow/v1",
  "key": "rfi",                       // unique within pack
  "pack": "construction",             // owning pack
  "version": "1.0.0",
  "business_goal": "…",               // (1)
  "personas": {                       // (2) reference the pack persona catalog
    "primary": "project_engineer",
    "supporting": ["project_manager", "site_engineer", "contractor"]
  },
  "trigger": {                        // (3) email | manual | schedule | webhook
    "type": "email", "source": "nango.outlook"
  },
  "inputs":  [{ "key": "rfi_email", "type": "email", "required": true }],   // (9)
  "connectors_required": ["nango.outlook", "composio.procore"],            // (8)
  "steps": [ /* (4)(5) ordered steps — see §5 */ ],
  "approvals": [                       // (6) declares which steps are approval gates
    { "step": "pm_review", "approver_persona": "project_manager" }
  ],
  "outputs": [{ "key": "response_email", "type": "email" }],               // (10)
  "business_value": "…"                // (11)
}
```

Definitions are validated against a JSON Schema in `packages/shared` before they can be
registered. Unknown step types, missing referenced connectors, or dangling `{{ }}`
references fail validation at registration time (not at runtime).

## 5. Step vocabulary (closed set — no arbitrary code)

Each step: `{ "id", "type", "config", "out" }`. The engine knows how to run each `type`:

| `type` | Does | Backed by |
|---|---|---|
| `connector.call` | Invoke a connector tool (read email, fetch record, create record, send) | Connector Hub |
| `document.parse` | Parse/OCR a file → text/structured | Knowledge (Docling) |
| `document.retrieve` | Semantic search over tenant docs | Knowledge (pgvector) |
| `ai.action` | Run a prompt template over context → text or JSON (extract/classify/draft/summarize) | Orchestrator |
| `approval` | Create a persona-routed human approval task; wait for decision | Approval Engine (Temporal signal) |
| `transform` | Map/format data via a restricted expression | in-engine |
| `notify` | Send a message (email/Teams) | Connector Hub |
| `branch` | Conditional next-step (phase 2) | in-engine |

**Context & data flow:** each step's output is stored in a per-run **context** under its id.
Later steps reference earlier data with a **restricted expression** (JSONPath-style),
never `eval`, e.g. `{{ steps.ocr.out.text }}`, `{{ inputs.rfi_email.attachments }}`.

## 6. Pack layout (files) → registry (DB)

Authored in the repo, seeded into a per-tenant DB registry (chosen model):

```
/packs/
  demo/                         # M2 reference pack (industry-neutral)
    pack.json                   # pack manifest: key, personas, connectors, version
    workflows/
      document_review.json      # proves: trigger → parse → ai.action → approval → notify
    prompts/
      summarize.md
  construction/                 # M3 (not in M2)
    pack.json
    workflows/ rfi.json  change_order.json  daily_report.json  …
    prompts/ …
```

- **Registry** loads + validates pack files, then upserts into DB (`workflow_packs`,
  `workflow_definitions`) scoped per tenant. Tenants **enable** packs and **pin versions**.
- Same seed pattern as M1 (`deploy/seed`): a job loads `/packs/*` into the registry.

## 7. Data model (Postgres, all RLS-scoped except the pack catalog)

- `workflow_packs` — `tenant_id, pack_key, industry, version, enabled, manifest jsonb`
- `workflow_definitions` — `tenant_id, pack_key, workflow_key, version, definition jsonb`
- `workflow_runs` — `tenant_id, run_id, pack_key, workflow_key, status, context jsonb,
  current_step, created_by, created_at, updated_at`  *(generalizes M1 `workflow_instances`)*
- `workflow_step_runs` — `tenant_id, run_id, step_id, type, status, input jsonb, output
  jsonb, error, started_at, ended_at`
- `approval_tasks` — `tenant_id, run_id, step_id, approver_role, status, decision,
  decided_by, comment, created_at, decided_at`

## 8. Execution flow

```
Gateway → POST /api/workflows/runs { pack, workflow, inputs }
  → Registry loads + validates the definition (tenant-scoped)
  → start Temporal PackWorkflow(run_id, definition, inputs)
      for each step in definition.steps:
        Step Engine resolves inputs from context (expressions)
        dispatch by type → activity:
          connector.call → Connector Hub    ai.action → Orchestrator
          document.*     → Knowledge         approval  → create task + await signal
          transform/notify → in-engine / Connector Hub
        persist workflow_step_runs; write result into context
      persist workflow_runs.status; emit audit events throughout
Approvals: POST /api/workflows/runs/{id}/approvals/{step} { decision, comment }
  → signals the waiting PackWorkflow (reuses M1 signal pattern)
```
Everything is durable (Temporal), tenant-scoped (RLS), authorized (Cerbos), and audited.

## 9. Persona & approval routing

- Each pack ships a **persona catalog** (e.g. Construction: Project Manager, Project
  Engineer, Site Engineer…). This is the RBAC basis the POC calls out.
- A pack-level **persona → platform-role** map binds personas to M1 roles
  (`owner/admin/member/viewer`) and Cerbos policies.
- An `approval` step names an `approver_persona`; the Approval Engine resolves it to the
  role(s) allowed to decide, creates the task, and enforces the decision via Cerbos.
- The engine stays industry-neutral — personas are pack data.

## 10. API surface (added to the workflows service, behind the gateway)

| Method + path | Purpose |
|---|---|
| `GET /api/workflows/packs` | List packs enabled for the tenant |
| `GET /api/workflows/definitions` | List available workflows (pack + key + version) |
| `POST /api/workflows/runs` | Start a run `{ pack, workflow, inputs }` |
| `GET /api/workflows/runs` · `/{id}` | List / inspect runs (status, steps, context) |
| `POST /api/workflows/runs/{id}/approvals/{step}` | Approve/reject a gate |
| `GET /api/workflows/approvals` | Tenant approval queue (feeds the Approvals UI) |
| `POST /internal/registry/reload` | Re-seed pack files → registry (server-to-server) |

## 11. The M2 demo pack (proof, industry-neutral)

`packs/demo/workflows/document_review.json`:
`trigger: manual → parse (a supplied doc) → ai.action (summarize, prompt=summarize.md) →
approval (approver_persona=reviewer) → notify (echo connector)`.
This exercises **every engine subsystem** (step engine, AI action, approval, connector,
context) without any industry semantics — and is the M2 acceptance test.

## 12. Milestone breakdown

- **M2 (this) — Framework + demo pack.** Schema, registry, executor, step engine, AI-action
  engine, approval engine, connector registry (+ Nango/Composio live), data model, API, seed.
- **M3 — Construction pack.** `rfi.json` first, then Change Order, Daily Report, Invoice
  Verification, Progress Reporting; Construction persona catalog; live Procore/Autodesk/
  Primavera/Buildertrend (Composio) + Outlook/Teams/SharePoint (Nango). New ADR for the pack.
- **M4 — Legal pack.** Contract Review first.

## 13. Definition of Done (M2)

- A pack folder validates and seeds into the per-tenant registry.
- The `demo` workflow runs end-to-end on Temporal: parse → AI summary → **human approval**
  → notify, with every step + the decision recorded in `workflow_step_runs` / `approval_tasks`
  and the audit log, all tenant-scoped and Cerbos-enforced.
- Adding a **new** workflow requires only a new JSON + prompt files — **no engine code change**
  (proved by shipping a second trivial demo workflow).
- Nango and Composio connectors are invocable through the Connector Hub (real calls when
  credentials are configured; graceful "not configured" otherwise).
- JSON Schema, migrations, seed, and smoke tests committed; ADR-0015 recorded.

## 14. Open questions / phase 2

- Branching/parallel steps and `schedule` triggers (Progress Reporting is weekly-scheduled).
- Retry/compensation policy per step type (Temporal supports it; expose in the schema).
- Pack signing/versioning strategy for tenant upgrades.
- Whether the FE workflow-authoring UI is a later milestone.
