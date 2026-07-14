# PROJECT_MEMORY.md — Industry AI OS

> **Single source of truth for project state.** This is the ground truth for current
> status, decisions, and constraints. It is NOT the design doc — see
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/adr/](docs/adr/) for that.

## Agent protocol (read this first, every session)

1. **Read this whole file before making any change.** Do not implement anything until
   you understand the current state, decisions, and constraints below.
2. Make changes **only in alignment** with the decisions/constraints here. If a change
   contradicts a constraint, stop and raise it with the user first.
3. **After finishing any change, append a Change Log entry** (bottom of this file):
   what changed, why, files modified, commands/tests run, next steps / pending issues.
   Update the relevant sections (status, completed, pending) too.
4. Keep it honest: record what was actually *verified* vs *written but not run*.

---

## 1. Current goal

**Milestone 1 — Platform Foundation.** Build the reusable, multi-tenant "Industry AI OS"
core only. **Zero industry-specific code.** Industries (insurance, construction, legal,
…) plug in later as configuration + workflow packs, targeting 80–90% shared code.

**Status: Milestone 1 build COMPLETE (authored + statically verified). Live stack not yet
booted — see constraint C1.**

## 2. Architecture (as built)

Monorepo, Python 3.11+, `uv` workspace. Gateway is the only public ingress; downstream
services trust only a gateway-minted, HMAC-signed tenant-context header.

```
Web App (separate track) → Gateway (REST+GraphQL) → services → infra
```

- **9 FastAPI services** (`services/`): `gateway`, `identity`, `authz`, `orchestrator`,
  `knowledge`, `workflows`, `connectors`, `audit`, `admin`.
- **Shared spine** (`packages/shared`, package `ai_os_shared`): settings, tenant_context,
  auth (JWT + signed internal context), db (async SQLAlchemy + RLS session), authz
  (Cerbos `check()`), audit emitter, llm (LiteLLM client), telemetry (OTel), health,
  errors, types, shared FastAPI app factory.
- **Infra** (`deploy/docker-compose.infra.yml`, 11 containers): postgres+pgvector,
  keycloak, cerbos, minio, redis/valkey, nats, temporal (+ui), langfuse, litellm,
  otel-collector, infisical (opt-in `secrets` profile).
- **Schema**: Alembic migrations `deploy/migrations/versions/0001_*`–`0003_*` — canonical
  tables + workflow pack framework tables + `user_profiles` (role/login_source), all with
  RLS + append-only audit trigger + least-priv grants.
- **App database is now Neon** (shared team Postgres, not the local container) — see D9.
  Keycloak/Temporal/Langfuse still use the local `docker-compose.infra.yml` Postgres.

## 3. Decisions (do not silently reverse)

| # | Decision | Note |
|---|---|---|
| D1 | Tenancy = **single Keycloak realm + Organizations = tenants**, shared Postgres + RLS | User-chosen. ADR-0000. `ctx.tenant_id` = Keycloak org id. |
| D2 | LLM = **Claude primary + OpenAI fallback** via LiteLLM aliases | User-chosen. `deploy/litellm/config.yaml`. |
| D3 | All build-vs-buy per the decision table | 15 ADRs in `docs/adr/` (0000–0014). |
| D4 | Every external tool sits behind an interface in `packages/shared` | Swappable; services never import a vendor client directly. |
| D5 | `tenant_id` on every tenant row; RLS enforced; app DB role has no BYPASSRLS | DB is last line of defense. |
| D6 | Gateway mints signed context from JWT; no service trusts client-supplied tenant id | `X-AIOS-Context`, HMAC. |
| D7 | ONE generic workflow only: `document_review_approval` (Temporal, human-in-loop) | No industry workflows in core. |
| D8 | Connector Hub is the ONLY layer touching third-party APIs | `Connector` ABC: `invoke(tool, args, config)`. |
| D9 | App DB (tenants/documents/chat/workflows/audit/user_profiles) moved to **Neon** (shared team dev Postgres); Keycloak/Temporal/Langfuse stay on the local container | User-chosen, 2026-07-10. App runtime connects as restricted `aios_app` role (`NOSUPERUSER NOBYPASSRLS`); migrations/seed connect as `neondb_owner`. |
| D10 | Self-service signup: **Keycloak stays the credential store/token issuer** (ADR-0001 unchanged); a new `user_profiles` table in the app DB carries platform-only fields (`role`, `login_source`) keyed by email | User-chosen, 2026-07-10. Public signups always land as `member` of the shared `demo` tenant — never owner/admin (privilege-escalation guard). |
| D11 | **Multiple industry frontends, one backend. Industry = config, driven by `packs/<industry>/pack.json` (`workspace` block), NOT code.** N landing pages (per industry) hit the same gateway; each calls `/workspace/config` to render its nav/theme/terminology. Data isolation stays at the tenant/RLS layer (Plan A) — industry differs the *interface + catalogue*, not data isolation | User-chosen, 2026-07-10. Adding an industry = adding a `pack.json`; no code change. Plan B (hard per-industry data isolation via separate tenants) deferred until real multi-industry customers onboard. |

## 4. Constraints / gotchas

- **C1 — RESOLVED 2026-07-10.** Docker daemon is up; the full stack boots and runs
  live (see Change Log). Originally: "daemon was down this session, stack never booted."
- **C2 — Commits are NOT being made.** Global git GPG signing uses a passphrase-protected
  key that can't prompt non-interactively (hangs). Per user, we **build without committing**;
  the user commits later with their signing setup. Do not attempt `git commit` unless asked.
- **C3 — Secrets**: `.env.example` only. Never commit `.env`. Boots green with empty
  `*_API_KEY` (LLM calls just fail at request time until keys are set). The live Neon
  connection strings (D9) also live only in the local `.env` (gitignored) — share with
  teammates out-of-band (password manager/DM), not by committing them anywhere.
- **C4 — Keycloak issuer split**: JWKS fetched via internal `KEYCLOAK_URL` (docker),
  `iss` validated against public `KEYCLOAK_ISSUER` (localhost:8081). Keep both aligned.
- **C5 — Windows host CRLF is NOT just a benign warning.** `deploy/seed/run.sh` and
  `deploy/scripts/health.sh` had CRLF line endings and **actually failed** at runtime
  inside their Linux containers (`set -euo pipefail` parsed as an invalid option named
  `pipefail<CR>`) — fixed 2026-07-10 (`sed -i 's/\r$//'`). Any shell script touched on
  this Windows host needs its line endings checked before it's trusted to run.
- **C6 — Keycloak access tokens on this realm carry no `sub` claim.** `ctx.user_id`
  (from `auth.py::context_from_jwt`) therefore resolves to `preferred_username`/email,
  not the Keycloak internal user UUID, everywhere in the platform. Any new code that
  joins on "the user id" from a `TenantContext` should join on **email**, not assume
  it's a UUID — this bit the first version of the `/me` + `user_profiles` join
  (2026-07-10 Change Log entry).

## 5. Completed (Milestone 1)

- [x] Workspace scaffold, `.env.example`, README, Makefile, docs skeleton.
- [x] `packages/shared` spine + 4 trust-boundary unit tests (pass).
- [x] Infra compose (11 containers) + config (Postgres init, Cerbos policies, LiteLLM,
      OTel, Keycloak realm with demo org + 4 users/roles).
- [x] Canonical schema migration (verified via `alembic upgrade head --sql`, exit 0).
- [x] All 9 services (all import cleanly; ruff clean; py_compile clean).
- [x] Full-stack compose (22 services, `docker compose config` valid) + seed job.
- [x] Helm chart (renders app services from values; infra via subcharts).
- [x] Smoke tests (health + DoD e2e + authz) with skip-if-stack-down.
- [x] 15 ADRs + ARCHITECTURE + MULTI_TENANCY + DEPLOYMENT + API docs + GraphQL SDL.
- [x] Live stack booted end-to-end on the user's machine (2026-07-10).
- [x] App DB migrated to shared Neon Postgres, with a least-priv `aios_app` role;
      RLS isolation live-verified (2026-07-10).
- [x] Self-service signup (`POST /auth/register`) + DB-backed `role`/`login_source`
      user profile, live-verified end-to-end (2026-07-10).

## 6. Verification status (what was actually run)

| Check | Result |
|---|---|
| `packages/shared` imports + 4 unit tests | ✅ pass (Python 3.13 venv) |
| All 9 `services/*/main.py` import `app` | ✅ pass (single venv, all deps) |
| `ruff check services packages` | ✅ clean |
| `py_compile` all `.py` | ✅ clean |
| Alembic migration offline SQL | ✅ exit 0, 189 lines DDL |
| `docker compose config` (infra + full) | ✅ valid, 22 services |
| GraphQL SDL export | ✅ `docs/api/graphql.schema.graphql` |
| **Live `docker-compose up` end-to-end** | ✅ all 9 services + infra healthy (2026-07-10) |
| Login → JWT → `/api/identity/me` (live, real Keycloak) | ✅ pass (2026-07-10) |
| Neon RLS isolation (tenant A/B/no-tenant reads) | ✅ pass (2026-07-10) |
| Self-service signup → auto-login → `/me` returns role+login_source | ✅ pass (2026-07-10) |
| Industry endpoints live: `/industries` + `/api/identity/workspace/config` (construction) | ✅ pass (2026-07-13) |
| Signup with `login_source` → industry workspace config, end-to-end | ✅ pass (2026-07-13) |
| Shared unit suite incl. `test_industry.py` (23 tests) | ✅ pass (2026-07-13) |
| Helm `helm template` render | ❌ NOT RUN (helm not installed) |

## 7. Pending tasks / next steps

**Immediate (finish Milestone 1 acceptance):**
- [x] Boot the stack — done 2026-07-10 (see Change Log).
- [x] Industry feature live-verified + first per-industry FE (Accounting) wired —
      done 2026-07-13 (see Change Log).
- [ ] **Author pack workflows (copilots) for accounting/legal/litigation** so those FEs
      have real workflow content (construction already has 5). Their `pack.json` currently
      has `workflows: []`.
- [ ] Wire the **Construction + Legal FEs** to the backend the same way as Accounting
      (separate repos; see `../Accounting/FRONTEND.md` for the template) once provided.
- [ ] Run `make smoke` against the live stack; confirm the DoD flow (login → chat →
      upload → RAG → approval workflow → audit) passes.
- [ ] Set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in `.env` to exercise chat/embeddings/RAG.
- [ ] Install helm and `helm template deploy/helm` to validate charts.
- [ ] Backfill `user_profiles` rows for the 4 original demo users (`owner/admin/member/
      viewer@demo.aios.local`) — they predate the signup flow (D10) and currently have
      no profile row, so `/me` returns `role: null, login_source: null` for them.
- [ ] Fix `services/admin/main.py:86,117` — compares `tenants.id` (uuid) against the
      tenant slug string with no cast; `/api/admin/tenant` 500s. Pre-existing, found
      2026-07-10, not fixed (out of scope of that task).

**Deferred / known gaps (NOT core; for later milestones):**
- [ ] **OCR** for scanned docs/images (PaddleOCR / cloud Doc AI) — only genuine capability
      gap surfaced by the Construction/Legal POC review. Docling handles digital docs only.
- [ ] **Tool-calling agents** in the orchestrator (LangGraph agent nodes that call Connector
      Hub tools mid-graph) — needed for "AI compares/calculates" POC steps.
- [ ] **Live Composio** connector (currently placeholder in `services/connectors/base.py`).
- [ ] **Nango** connector kind + ADR (new build-vs-buy; complements MCP/Composio, not in
      original decision table). Raised by the POC docs; connectors NOT yet confirmed by user.
- [ ] Temporal Schedules (cron triggers) + PDF report generation (small utilities).

## 8. Key file map (for a new agent)

- Shared contract: `packages/shared/src/ai_os_shared/` (`app.py`, `auth.py`, `db.py`,
  `authz.py`, `audit.py`, `llm.py`, `tenant_context.py`, `settings.py`).
- Each service: `services/<name>/src/<name>/main.py`.
- Deploy: `deploy/docker-compose.yml` (full), `deploy/docker-compose.infra.yml`,
  `deploy/Dockerfile.service`, `deploy/migrations/` (incl. `0003_user_profiles.py`),
  `deploy/seed/`, `deploy/helm/`.
- Docs: `docs/ARCHITECTURE.md`, `docs/adr/`, `docs/MULTI_TENANCY.md`, `docs/api/`.
- Auth: login = `POST /auth/token` (unchanged); signup = `POST /auth/register`
  (gateway) → `POST /internal/register` (identity, not exposed through the generic
  proxy — see D10, C6). `.env`: `DATABASE_URL`/`DATABASE_URL_SYNC` now point at Neon
  (D9), `POSTGRES_*` vars remain for the local Keycloak/Temporal/Langfuse Postgres.
- Demo login (after seed): `owner@demo.aios.local` / `Passw0rd!` (also admin/member/viewer)
  — these 4 predate D10 and have no `user_profiles` row yet (see §7 pending).

---

## Change Log (append newest at the bottom)

### 2026-07-08 — Milestone 1 foundation built
- **What:** Scaffolded the entire platform: shared spine, 9 services, infra compose (11
  containers), canonical schema migration, full-stack compose + seed, Helm chart, smoke/e2e
  tests, 15 ADRs, ARCHITECTURE + API docs + GraphQL SDL.
- **Why:** Milestone 1 — reusable platform foundation, no industry code.
- **Files:** entire repo (see §8).
- **Verified:** shared unit tests, all-9-service imports, ruff, py_compile, Alembic offline
  SQL, `docker compose config`, GraphQL SDL export (see §6). Live stack NOT booted (C1).
- **Decisions captured:** D1 (tenancy) and D2 (LLM) chosen by user; rest per decision table.
- **Next:** boot the stack and run `make smoke`; address deferred gaps only on user go-ahead.

### 2026-07-08 — POC feasibility review (Construction + Legal, Nango + Composio)
- **What:** Assessed the 10 POC workflows against the built code (no code changes).
- **Outcome:** Feasible with zero core changes; work lands in the industry-pack layer.
  Only true capability gap = OCR. Nango = new connector kind (+ADR). Composio live +
  tool-calling agents = wiring. Recorded in §7 deferred gaps.
- **Files:** none changed (assessment only).
- **Next:** await user confirmation on connectors before building Nango/Composio/OCR.

### 2026-07-08 — Wired landing-page login to the backend + fixed FE↔API alignment
- **Context:** A separate frontend exists at `industry-ai-os/` (TanStack Start + React 19,
  Lovable-connected, its OWN git repo — see its `AGENTS.md`: don't rewrite its history).
  It's a landing page with a working Login/Signup modal whose handlers were **stubs**.
- **What changed (backend):**
  - `services/gateway/src/gateway/main.py`: added `CORSMiddleware` (browser origin was
    blocked — a hard blocker for any FE call); changed `POST /auth/token` to take a JSON
    **body** (`{username,password}`) instead of query params (no password in URLs).
  - `packages/shared/.../settings.py`: added `CORS_ORIGINS` setting + `cors_origin_list`.
  - `.env.example`: documented `CORS_ORIGINS`.
  - `tests/smoke/conftest.py`: `_token` now posts JSON body (matches the endpoint change).
- **What changed (frontend, `industry-ai-os/`):**
  - `src/lib/api.ts` (new): typed gateway client — `login()` (→ `/auth/token`, stores
    token), `getMe()` (→ `/api/identity/me`), `VITE_API_URL` base, bearer on every call.
  - `src/routes/index.tsx`: login form now calls `api.login`, shows real errors
    (401 → "Invalid email or password") and greets with tenant + roles from `/me`.
  - `.env.example` (new): `VITE_API_URL=http://localhost:8080`.
- **Verified:** backend ruff clean, py_compile clean. FE typecheck: see next entry
  (npm install was running). Live login NOT exercised (stack not booted — C1).
- **Alignment gaps recorded (NOT fixed — need product/backend decision):**
  - **Signup is still a stub** — backend has NO public self-registration (Keycloak
    registration disabled; users created by a tenant admin via identity service). Decide:
    add a public `/auth/register` (+ tenant provisioning) or make signup "request access".
  - **No authenticated workspace/dashboard route** in the FE — login succeeds but there's
    nowhere to land. Landing page only. A real app shell is a separate build.
  - **"Continue with SSO" not wired** — needs the Keycloak OIDC redirect flow (the
    `/auth/token` direct-grant is dev-only; production should use the redirect flow).
  - Confirm the FE dev origin is in `CORS_ORIGINS` (added 3000/5173/8080).
- **Next:** await user decision on signup + workspace routes + SSO before building them.

### 2026-07-08 — Fixed gateway/frontend port collision (8080 → 8000)
- **Why:** the frontend dev server (Vite/TanStack) runs on **:8080**, which is exactly
  where the gateway was published — they can't share a host port, and `VITE_API_URL`
  pointed at the frontend itself.
- **What:** gateway published port moved to **8000** (`deploy/docker-compose.yml`);
  `VITE_API_URL` → `http://localhost:8000`; updated `deploy/scripts/health.sh`,
  `tests/smoke/conftest.py`, `docs/DEPLOYMENT.md`, `docs/api/README.md`.
  `CORS_ORIGINS` already allows `http://localhost:8080` (the FE origin) — correct.
- **Net:** frontend = :8080, gateway/API = :8000. FE `.env` must set `VITE_API_URL=http://localhost:8000`.

### 2026-07-09 — Milestone 2 design: Workflow Pack Framework (DESIGN ONLY, no code)
- **What:** Wrote the M2 design — a data-driven workflow engine where workflows are
  declarative JSON definitions run by one generic Temporal interpreter (`PackWorkflow`),
  not per-workflow code. Industries ship as "packs" (JSON + prompts + persona/connector
  config); adding an industry changes no platform code.
- **Why:** Realizes the 80–90%-reuse thesis (confirmed by `Construction_Legal_AI_OS_POC.docx`
  and its 11-part template). Supersedes M1's single hardcoded `DocumentReviewApproval`.
- **Decisions (user-confirmed):** M2 = **framework + a trivial industry-neutral `demo` pack
  only** (no Construction logic — that's M3). Definitions authored as **JSON files in repo →
  seeded into a per-tenant DB registry**. Design doc + ADR written **before** any code.
- **Files:** `docs/adr/0015-workflow-pack-framework.md` (ADR), `docs/MILESTONE_2.md` (full
  spec: schema, closed step vocabulary, data model, execution flow, API, demo pack, DoD),
  `docs/adr/README.md` (index += 0015).
- **Verified:** N/A (documents only; no code run).
- **Next:** on approval of the spec, build M2 — definition schema + validator, Workflow
  Registry, `PackWorkflow` executor, Step Engine, AI Action Engine, Approval Engine,
  Connector Registry (Nango/Composio live), data-model migration, seed, smoke tests.
  Then M3 = Construction pack (RFI first).

### 2026-07-09 — Milestone 2 build (engine core + packs) — VERIFIED offline
- **What:** Built the Workflow Pack Framework's core and authored the construction pack.
  - `packages/shared/src/ai_os_shared/workflow/`: `schema.py` (WorkflowDefinition +
    PackManifest, 11-part template, validation), `expr.py` (safe `{{ }}` resolver, no
    eval), `engine.py` (generic `WorkflowEngine` — step sequencing, context, approval as
    a handler), `registry.py` (load/validate packs).
  - `packs/demo/` (document_review) + `packs/construction/` (rfi, change_order,
    daily_report, invoice_verification, progress_report) with prompts. All validate.
  - `services/workflows/src/workflows/step_handlers.py`: DI factory bridging step types
    to LLM / Connector Hub / Knowledge / approval. **Connector.call routes to the
    Connector Hub — this is where Nango/Composio plug in once creds exist.**
  - `deploy/migrations/versions/0002_workflow_pack_framework.py`: workflow_packs,
    workflow_definitions, workflow_runs, workflow_step_runs, approval_tasks (+RLS).
- **Verified:** 12 unit tests pass (schema, expr, engine end-to-end incl. approval; all
  6 pack definitions validate); ruff clean; migration 0002 applies via offline SQL (exit 0).
- **NOT yet built / not verified (needs live stack + creds):** the Temporal `PackWorkflow`
  executor, the DB-backed run persistence, the workflows HTTP API (runs/approvals), pack
  seeding into the DB registry, and the real Connector Hub entries for nango.*/composio.*
  (they return "not_configured" until configured). `step_handlers.py` is compile-checked,
  not run.
- **Decision recorded:** `docs/adr/0015` + `docs/MILESTONE_2.md`.
- **Next:** (1) wire the Temporal PackWorkflow + activities + approval signal using
  `WorkflowEngine` + `build_handlers`; (2) add the workflows API + DB runner + pack seed;
  (3) register Nango/Composio connectors in the Connector Hub with the tools the
  construction definitions call (get_message, get_drawings, update_rfi, etc.) once the
  user provides accounts/credentials.

### 2026-07-10 — Full stack brought up under Docker Compose — VERIFIED running
- **What:** First successful end-to-end boot of the whole stack on the user's Windows
  machine. Fixed the blockers that surfaced on a clean `up`:
  1. **Disk exhaustion** (C: hit 100%): the `knowledge` image pulled the full torch+CUDA
     stack (~8 GB) via Docling, corrupting layer writes. Slimmed
     `services/knowledge/pyproject.toml` — removed `docling` and `llama-index-core` (both
     were already guarded/optional in `parsing.py`, which falls back to plain-text
     extraction + char-window chunking). Image ~8 GB → ~1 GB.
  2. **Cerbos crash-loop:** `common_roles` derived-roles file lived in
     `deploy/cerbos/policies/_schemas/` — but `_schemas` is a Cerbos-reserved dir (schemas
     only), so policies there aren't loaded → all 7 resource policies failed their import.
     Moved it to `deploy/cerbos/policies/derived_roles.yaml`; removed the empty `_schemas`.
  3. **All healthchecks used a bash-only `/dev/tcp` probe** but the images use dash/busybox
     or no shell. Rewrote them: app services (`docker-compose.yml` `x-service-base`) +
     `litellm` → `python -c urllib.urlopen(...)`; `langfuse` → `wget`; `nats` (scratch
     image, no shell) → healthcheck removed (nothing gates on it).
- **Verified:** `docker compose ps` — all 9 app services + core infra **healthy**;
  `seed` and `minio-init` `Exited (0)`; gateway `GET /healthz` and `/readyz` return ok.
  `langfuse` settles to healthy ~1-2 min after boot (runs its own migrations; non-critical).
- **Also added:** `ai-backend/RUNNING.md` — run guide for teammates (first-run vs daily,
  `.env.example` vs `.env.local.example`, ports, demo logins, troubleshooting).
- **Note for compose:** always run from `ai-backend/` with
  `docker compose -f deploy/docker-compose.yml --env-file .env <cmd>`; use `.env.example`
  (Docker hostnames), NOT `.env.local.example` (localhost / host-uvicorn only).
- **Next (unchanged):** wire Temporal PackWorkflow executor + workflows API + DB runner +
  pack seed; register Nango/Composio connectors once creds exist.

### 2026-07-10 — App DB migrated to shared Neon Postgres (D9)
- **What:** Wired the platform's own app data (tenants/documents/chat/workflows/audit)
  onto a shared team Neon Postgres instance, decoupled from the local Docker Postgres
  container (which still backs Keycloak/Temporal/Langfuse — untouched).
  - Created a restricted `aios_app` role on Neon (`NOSUPERUSER NOBYPASSRLS`) + enabled
    `vector`/`uuid-ossp` extensions.
  - `.env`: `DATABASE_URL` (async runtime) → Neon via `aios_app`; `DATABASE_URL_SYNC`
    (migrations/seed) → Neon via `neondb_owner` (needed for `CREATE EXTENSION`/`GRANT`).
  - Ran migrations 0001+0002 and the seed script against Neon; all 9 app services
    recreated against the new `.env`.
  - **Fixed a real bug found along the way**: `deploy/seed/run.sh` and
    `deploy/scripts/health.sh` had Windows CRLF line endings, which broke
    `set -euo pipefail` inside their Linux containers (parsed as an invalid option
    named `pipefail<CR>`) — the seed container was silently exiting 2. Stripped `\r`
    from both files; see C5 (corrected — this is not just a benign warning).
- **Why:** User wants the whole team developing against one shared dev database
  instead of everyone's own local Postgres container.
- **Verified live:** login → JWT → `/api/identity/me` over Neon; `aios_app` role
  attributes (`rolsuper=false`, `rolbypassrls=false`); RLS isolation (tenant A can't
  see tenant B's rows; no-tenant-set sees nothing); `/api/knowledge/documents` reads
  correctly (empty, as expected).
- **Found, not fixed (pre-existing, unrelated):** `services/admin/src/admin/main.py:86,117`
  — `WHERE id = :id OR keycloak_org_id = :id OR slug = :slug` compares the uuid `id`
  column against a non-uuid string with no cast; `/api/admin/tenant` 500s regardless of
  DB backend. Flagged in §7, not fixed (out of scope of this change).
- **Files:** `.env` (not committed — gitignored), `deploy/seed/run.sh`,
  `deploy/scripts/health.sh`.
- **Next:** distribute the Neon connection string to teammates out-of-band (not
  committed); consider wiring Infisical (ADR-0014, already opt-in in the stack) instead
  of passing the raw string around by hand.

### 2026-07-10 — Self-service signup + DB-backed role/login_source (D10)
- **What:** Built real public signup, on top of the existing "login works, signup is a
  stub" state. Kept Keycloak as the credential store/token issuer (ADR-0001 unchanged)
  — added a `user_profiles` table in the app DB for platform-only fields.
  - `deploy/migrations/versions/0003_user_profiles.py`: `user_profiles` table
    (`tenant_id`, `keycloak_user_id`, `email`, `role` CHECK'd to the existing
    `owner/admin/member/viewer` vocabulary, `login_source` CHECK'd to
    `accounting/legal/litigation/construction`), RLS + grants, same pattern as every
    other table.
  - `services/identity/src/identity/main.py`: new `POST /internal/register` (exempt
    from the context-header requirement via the existing `/internal/*` convention —
    server-to-server only, never proxied through the gateway's generic authenticated
    route). Looks up the `demo` tenant's Keycloak org id, creates the Keycloak user,
    assigns the `member` role (never owner/admin — public signup must not be able to
    grant elevated privileges on a shared tenant), writes the `user_profiles` row.
    `GET /me` extended to return `role`/`login_source`.
  - `services/gateway/src/gateway/main.py`: new public `POST /auth/register` — calls
    identity's `/internal/register`, then immediately performs the same Keycloak
    password-grant as `/auth/token` so signup auto-logs-in.
  - Frontend (`frontend-industory-ai-os`, separate repo): `SignupForm` no longer fakes
    a delay — calls the real `register()`, added a "Your industry" (`login_source`)
    dropdown, auto-navigates into `/app` on success. Dropped the now-meaningless
    "Company" field (signup joins the single shared `demo` tenant, not a new org).
- **Bug found + fixed during build:** this Keycloak client's **access tokens carry no
  `sub` claim**, so `ctx.user_id` (`auth.py::context_from_jwt`) resolves to the email
  everywhere on this platform, not a UUID. First version of the `/me` → `user_profiles`
  join used `keycloak_user_id`, which never matched; fixed to match on email too. See
  new constraint C6 — anything else that joins on "the user id" from `TenantContext`
  should account for this.
- **Decisions (user-confirmed):** Keycloak stays the auth engine, DB only carries
  profile fields (D10); signup joins the single shared `demo` tenant, not a new tenant
  per signup; role vocabulary stays `owner/admin/member/viewer` (not renamed to
  `owner/manager/viewer`) — `login_source` is the only new dimension.
- **Verified live:** signup → real Keycloak user created in `demo` org → `user_profiles`
  row written → auto-login → `/me` returns `role: "member"`, `login_source` as chosen;
  bad `login_source` → 422; duplicate email → clean error (not silent). Frontend:
  `tsc --noEmit` clean, dev server boots clean, CORS preflight on `/auth/register` OK.
  **Not verified:** an actual browser click-through (no browser access from this
  session) — ask the user to confirm the UI feels right.
- **Known gap:** the 4 original demo users (`owner/admin/member/viewer@demo.aios.local`)
  predate this flow and have no `user_profiles` row — `/me` returns
  `role: null, login_source: null` for them until backfilled (see §7).
- **Files:** `deploy/migrations/versions/0003_user_profiles.py`,
  `services/identity/src/identity/main.py`, `services/gateway/src/gateway/main.py`;
  frontend: `src/api/client.ts`, `src/routes/index.tsx`.
- **Next:** backfill profile rows for the 4 demo users; fix the unrelated
  `services/admin` bug if desired; user to confirm real-browser signup/login UX.

### 2026-07-10 — Multi-industry frontends: config-driven industry registry (D11, Plan A)
- **What:** Backend support for N industry-specific landing pages against one backend.
  Industry is now **configuration** (`packs/<industry>/pack.json`), surfaced to any FE.
  - `packages/shared/.../workflow/schema.py`: `PackManifest` gained an optional
    `workspace` block (`WorkspaceConfig` + `NavItem`: display_name, tagline, theme, nav,
    entities, terminology, copilots). Optional → the generic demo pack still validates.
  - `packages/shared/.../industry.py` (NEW): config-driven registry. Scans
    `packs/*/pack.json`, skips `industry == "generic"`, exposes `list_industries()`,
    `get_industry(key)`, `industry_keys()`, `reload()`. Packs-dir resolution:
    `$AIOS_PACKS_DIR` → walk-up for `packs/` → `/app/packs` → `./packs` (works in
    container AND source/test, since `ai_os_shared` installs to site-packages and can't
    walk to `packs/` at runtime).
  - Pack manifests: added `workspace` to `packs/construction/pack.json` (matches its FE:
    RFIs/change-orders/daily-reports/submittals/drawings/invoices) and authored NEW
    `packs/accounting/pack.json` (invoices/POs/reconciliation/close/reporting — matches
    accounting-cyan FE), plus starter `packs/legal/` + `packs/litigation/` (workspace only,
    no workflows yet).
  - `services/identity/main.py`: `GET /industries` (authed list) + `GET /workspace/config`
    (current user's industry workspace, resolved from their `login_source`; bare config
    for profile-less legacy users). Signup now validates `login_source` against
    `industry_keys()` — the hardcoded `LOGIN_SOURCES` set is GONE (that was the
    "add an industry = edit code" bottleneck).
  - `services/gateway/main.py`: public `GET /industries` (served straight from the
    registry, no downstream call) + added to `_PUBLIC_PATHS` so signup dropdowns work
    pre-login. `/workspace/config` reached via the normal authed proxy
    (`/api/identity/workspace/config`).
  - `deploy/Dockerfile.service`: `COPY packs /app/packs` + `ENV AIOS_PACKS_DIR=/app/packs`
    (cached once before the per-SERVICE divergence) so every image can read the registry.
- **FE contract (for the FE dev):** pre-login `GET /industries` → `[{key,name,tagline,theme}]`
  for the signup selector; post-login `GET /api/identity/workspace/config` →
  `{login_source, industry, workspace:{display_name,theme,nav[],entities[],terminology,
  copilots[]}, workflow_packs[]}`. Same backend for all 3+ FEs; only this config differs.
- **Reference FEs seen:** construction-ai-os.vercel.app, accounting-cyan.vercel.app
  (used to shape the two workspace configs). buildflow-ai-nine = UI style ref.
- **Verified:** industry registry + `load_all_packs` resolve all 5 packs (4 industries +
  generic demo excluded); full shared suite **23 tests pass** (incl. new
  `tests/test_industry.py`, 7 cases); ruff clean on all changed files; py_compile clean.
  **NOT yet run live:** endpoints not exercised against the running stack (needs a
  `--build` of gateway + identity to pick up the new code + packs in-image).
- **Decision:** D11 (industry = config; Plan A — interface differs, data stays
  tenant-scoped). Plan B (per-industry data isolation) explicitly deferred.
- **Next:** rebuild gateway + identity (`docker compose ... up -d --build gateway identity`)
  and smoke-test `/industries` + `/workspace/config`; author accounting/legal/litigation
  workflows when those FEs are finalized; FE dev wires the 3 landing pages to the contract.

### 2026-07-13 — Industry feature verified live + first per-industry FE (Accounting) wired
- **What:** Took the D11 industry work from "authored" to "running live", and wired the
  first dedicated per-industry frontend against it.
- **Backend — live-verified (no code change beyond the healthcheck/seed fixes below):**
  - Rebuilt gateway + identity; `GET /industries` returns all 4 industries
    (accounting/construction/legal/litigation) with name+theme; `GET /api/identity/
    workspace/config` returns the caller's industry workspace (construction verified:
    amber theme, RFIs nav, `document→Drawing` terminology, 5 copilots). Legacy demo users
    (no `login_source`) correctly get a null/bare config.
  - Signup→industry flow verified end-to-end: `POST /auth/register` with
    `login_source=construction` → Keycloak user in demo org → auto-login → workspace config.
  - **Ops fixes made while getting there (compose/seed only):**
    (1) `deploy/docker-compose.infra.yml`: rewrote litellm/langfuse/nats healthchecks that
    used the bash-only `/dev/tcp` trick (images are dash/busybox/scratch) — litellm/app →
    `python urllib`, langfuse → `wget $(hostname -i)`, nats healthcheck removed; litellm
    `start_period`→150s. (2) `deploy/docker-compose.yml`: orchestrator's dependency on
    litellm changed `service_healthy`→`service_started` (litellm's /health warms up ~2-3
    min on cold boot and was aborting the whole `up`). (3) Cerbos: `common_roles` derived
    roles moved out of the reserved `_schemas/` dir. (4) `knowledge` image slimmed (dropped
    docling/torch, ~8GB→~1GB) after a disk-full build failure. (5) **Seed image must be
    rebuilt after any new migration** — a stale seed image (pre-`0003`) failed with
    "Can't locate revision '0003'" and never provisioned the demo Keycloak org, which
    surfaced downstream as signup "Organization not found" + login "JWT carries no
    organization/tenant claim". Rebuild+run of seed fixed it. See RUNNING.md.
- **Frontend — first industry FE wired (separate repo `../Accounting/`, NOT in this repo):**
  - The `Accounting/` FE (TanStack Start, same scaffold as the deleted demo `Landing-Page/`)
    was on a `DUMMY_AUTH`/localStorage mock. Flipped to the real gateway: signup →
    `/auth/register` with `login_source:"accounting"`; pages wired to real endpoints
    (dashboard→health/documents/audit, workflows, approvals→decide, connectors, documents,
    admin, assistant, settings). Analytics/knowledge/doc-intelligence/close-checklist left
    as dummy (no backend source yet). Documented in `../Accounting/FRONTEND.md` (the
    template for the Construction + Legal FEs to come).
  - `Landing-Page/` (the generic demo FE) was deleted by the user — superseded by the
    per-industry FEs. Not referenced by the backend.
- **Verified:** live curl of `/industries` + `/workspace/config` + full signup flow;
  FE typechecked-by-review only (its `node_modules` not installed here).
- **Next:** author accounting/legal/litigation pack workflows (copilots) so those FEs have
  real workflow content; wire the Construction + Legal FEs the same way when provided;
  backfill `user_profiles` for the 4 legacy demo users (still §7).

### 2026-07-13 — Workspace-aware AI Assistant (intent detection + Mode 2) in the orchestrator
- **What:** Turned the generic orchestrator `/chat` into the per-industry-workspace AI
  Assistant per spec. Conversation/intent/context live in the assistant; planning,
  execution, connectors, approvals stay in the workflow/orchestrator services (only
  *called*, never reimplemented). No fabrication of data/results anywhere.
  - `packages/shared/.../settings.py`: new `ASSISTANT_MODE` (alias `ASSISTANT_MODE`,
    default `strict_lenient`) — `strict` | `strict_lenient` (Mode 2, default) | `lenient`.
    Behavior changes via this var ONLY; no logic edits needed to switch modes.
  - `services/orchestrator/.../assistant.py` (NEW): `Mode`/`Intent` enums;
    `resolve_workspace()` (explicit `workspace` arg → else user's `login_source`, via the
    D11 `ai_os_shared.industry` registry); `classify_intent()` (LLM→JSON, 8 intents,
    fails safe to general_question); `build_system_prompt()` (workspace name +
    capabilities + terminology + response-format guidance + the mode's unrelated-question
    policy); `workspace_reminder()` + `last_assistant_had_reminder()` (Mode-2 reminder,
    never twice in a row).
  - `services/orchestrator/.../main.py`: `/chat` now resolves workspace → detects intent →
    `_gather_backend_data()` pulls REAL data for that intent from existing APIs
    (knowledge `/retrieve`; workflows `/workflows` for status/approvals, forwarding the
    signed context header) → answers with the workspace-aware system prompt → appends the
    Mode-2 reminder only for workspace-UNRELATED intents (general_question/conversation).
    Response adds `intent` + `workspace`; audit metadata records them. `/chat/stream` made
    workspace-aware (same system prompt + reminder tail). ChatRequest gained `workspace`.
    **Honesty guardrail:** workflow-execution/document-analysis intents get a data block
    that lists the workspace's configured workflows + the tenant's real runs and instructs
    the model to identify + collect inputs, and to state plainly that only
    `document_review_approval` runs end-to-end today (the pack-workflow Temporal executor
    is still pending) — never invent a run id or a result.
  - `.env.example`: documented `ASSISTANT_MODE`.
- **Verified:** ruff clean + py_compile clean on all changed files; pure logic smoke-tested
  offline (workspace resolves from packs; reminder text matches spec; mode parse defaults
  to Mode 2; reminder-dedup works; system prompt carries workspace + no-fabricate + format).
  **NOT yet run live:** intent classification + real answers need the orchestrator rebuilt
  and an LLM key set (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`).
- **Known limitation:** conversation context beyond history (current project/uploaded doc/
  current workflow) is not yet persisted — workspace comes per-request and "current
  workflow" is read live from the workflows list. Add a session-context store when needed.
- **Next:** rebuild orchestrator (`up -d --build orchestrator`), set an LLM key, and
  exercise the intents live; wire pack-workflow execution (Temporal PackWorkflow) so
  workflow-execution intents can actually start non-review workflows.

### 2026-07-13 — Assistant failure handling (backend SSE error frames + FE surfacing)
- **Why:** With no valid LLM key, litellm returned 401 and the chat stream produced zero
  tokens; the Accounting FE silently fell back to a canned `dummyReply()`, so a real
  backend failure looked like a bland answer. Fixed both ends.
- **Backend (`services/orchestrator/main.py`):**
  - `/chat`: wrapped the LLM call in try/except → logs the real error, persists the user
    turn, and raises `UpstreamError(LLM_UNAVAILABLE)` (clean 502 with a friendly message;
    never leaks the raw provider/proxy error).
  - `/chat/stream`: the generator now catches LLM errors and emits an SSE
    `data: {"error": "<friendly>"}` frame + `[DONE]` (instead of a broken/empty stream);
    switched all SSE framing to `json.dumps` (robust escaping); persists only the user turn
    on error (no empty assistant message). Added a `structlog` logger + `LLM_UNAVAILABLE`.
- **Frontend (`Accounting/`):**
  - `api/client.ts`: `chatStream` yield type now includes `error?: string`.
  - `routes/app.assistant.tsx`: `send()` surfaces failures — reads the `error` frame,
    treats an empty stream as an error, and catches thrown `ApiError`; shows a distinct
    destructive-styled ⚠️ bubble (`Msg.error`). The canned `dummyReply` is now gated behind
    an explicit `OFFLINE_DEMO=false` toggle (kept for no-backend UI demos), never a silent
    error mask.
- **Verified:** orchestrator ruff + py_compile clean; Accounting `tsc --noEmit` exit 0.
  **NOT yet run live** (needs orchestrator rebuild). Root cause of the 401 remains an env
  issue: `ANTHROPIC_API_KEY` empty + OpenAI key over quota — set a valid provider key and
  `up -d` (see that troubleshooting note).
- **Next:** rebuild orchestrator (`up -d --build orchestrator`) to activate; once a valid
  LLM key is set, verify a real answer + the Mode-2 reminder.

### 2026-07-14 — M3 (Accounting) start: invoice pack definition + Nango connector (sandbox)
- **What:** Began the Accounting Executive Prototype (M3). User-confirmed scope: workflow =
  **Invoice Verification** (AP invoice-to-approval); connector strategy = **sandbox first,
  Nango-compatible from day one** (ADR-0018).
  - `packs/accounting/workflows/invoice_verification.json` (NEW): the flow as a validated
    definition — read_email → extract (document.parse/OCR) → search_vendor → search_existing
    bills → **validate** (AI: vendor match + duplicate + tax) → **summary** (AI + approve/
    reject rec) → **approval** (controller) → create_bill (after approval) → notify_vendor.
    `connector.call` steps use the Nango proxy style: `tool` = HTTP method, `arguments` =
    `{endpoint, query/body}` (e.g. `GET /vendor`, `POST /bill`) — unchanged sandbox→live.
  - `packs/accounting/prompts/invoice_validate.md` + `invoice_summary.md` (NEW): the
    accounting logic (dedup/tax/field checks + recommendation), instructed to mark missing
    data "unknown" — never fabricate.
  - `packs/accounting/pack.json`: `connectors: [nango.gmail, nango.quickbooks]`,
    `workflows: [invoice_verification]`.
  - `services/connectors/base.py`: NEW generic `NangoConnector` (per-provider instance) —
    `invoke(method, {endpoint,query,body}, config)` → Nango proxy when creds present, else
    **sandbox** provider-shaped fixtures flagged `_sandbox: true` (`_gmail_sandbox`,
    `_quickbooks_sandbox`). Registered `nango.gmail` + `nango.quickbooks` in `registry.py`.
  - `packages/shared/settings.py`: `NANGO_SECRET_KEY` (empty ⇒ sandbox) + `NANGO_HOST`.
  - Docs: ADR-0018 (Nango) + adr/README index.
- **Verified:** pack loader validates all 5 packs incl. the new definition (9 steps, types
  correct, connectors match, approval references a real step); ruff + py_compile clean;
  Nango sandbox smoke test returns the exact shapes the definition consumes (vendor `id`,
  empty bills = no-dup, created bill, gmail from/attachments).
- **Decision:** D-Nango via ADR-0018 (sandbox-first proxy). Confirms D11-era plan.
- **NOT built yet (next):** the `PackWorkflow` Temporal executor + DB run persistence +
  `POST /workflows/{key}` run/status API + pack seeding; a sandbox `document.parse`
  (canned extracted invoice) so the demo flows without OCR; wire the assistant's
  workflow-execution intent to start the run; the executive dashboard KPIs. Then live Nango
  (OAuth connection per tenant + response field-mapping) and OCR for scanned invoices.

<!-- New agents: append your entry above this line. -->
