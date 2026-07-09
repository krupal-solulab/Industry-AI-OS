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
- **Schema**: one Alembic migration `deploy/migrations/versions/0001_*` — canonical
  tables + RLS on all tenant-owned tables + append-only audit trigger + least-priv grants.

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

## 4. Constraints / gotchas

- **C1 — Docker daemon was DOWN this session.** The full stack was **never booted**.
  Everything was verified statically/offline (see §6). First run: `make up` then
  `make health` — expect first-boot debugging of image tags/healthchecks.
- **C2 — Commits are NOT being made.** Global git GPG signing uses a passphrase-protected
  key that can't prompt non-interactively (hangs). Per user, we **build without committing**;
  the user commits later with their signing setup. Do not attempt `git commit` unless asked.
- **C3 — Secrets**: `.env.example` only. Never commit `.env`. Boots green with empty
  `*_API_KEY` (LLM calls just fail at request time until keys are set).
- **C4 — Keycloak issuer split**: JWKS fetched via internal `KEYCLOAK_URL` (docker),
  `iss` validated against public `KEYCLOAK_ISSUER` (localhost:8081). Keep both aligned.
- **C5 — Windows host**: line-ending warnings (CRLF) are expected/benign.

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
| **Live `docker-compose up` end-to-end** | ❌ NOT RUN (C1 — daemon down) |
| Helm `helm template` render | ❌ NOT RUN (helm not installed) |

## 7. Pending tasks / next steps

**Immediate (finish Milestone 1 acceptance):**
- [ ] Boot the stack: `make up`, then `make health`; fix any first-boot image/healthcheck issues.
- [ ] Run `make smoke` against the live stack; confirm the DoD flow (login → chat →
      upload → RAG → approval workflow → audit) passes.
- [ ] Set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in `.env` to exercise chat/embeddings/RAG.
- [ ] Install helm and `helm template deploy/helm` to validate charts.

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
  `deploy/Dockerfile.service`, `deploy/migrations/`, `deploy/seed/`, `deploy/helm/`.
- Docs: `docs/ARCHITECTURE.md`, `docs/adr/`, `docs/MULTI_TENANCY.md`, `docs/api/`.
- Demo login (after seed): `owner@demo.aios.local` / `Passw0rd!` (also admin/member/viewer).

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

<!-- New agents: append your entry above this line. -->
