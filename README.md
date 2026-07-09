# Industry AI OS

A reusable, multi-tenant **AI Operating System** — one platform meant to power many
industries (insurance, construction, legal, healthcare, manufacturing, accounting…)
by sharing 80–90% of its code. Industries plug in later as **configuration +
workflow packs**, never as forks.

> **Milestone 1 — Platform Foundation (this repo).**
> Builds the reusable platform core only. **Zero industry-specific code.**
> Industry workflows arrive in later milestones.

---

## Principles

1. **Buy/reuse before you build.** Every heavy capability is a mature OSS tool wrapped
   behind a clean internal interface in [`packages/shared`](packages/shared) — never forked, never reimplemented.
2. **Everything is modular.** Each capability is a service with a defined interface, swappable without touching callers.
3. **Everything is multi-tenant.** Every table, request, log line, and document carries a `tenant_id`. Postgres RLS enforces it.
4. **The UI never talks to enterprise systems directly.** All external access goes through the **Connector Hub**.
5. **Deployable three ways from day one:** on-prem (Docker Compose), cloud (Helm/K8s), and multi-tenant SaaS.
6. **Audit everything, observe everything.** Every state change is auditable; every LLM call is traced.

## Architecture (layers)

```
Web App (separate track)
      ↓  REST + GraphQL only
API Gateway   — auth (Keycloak JWT), tenant-context injection, rate limiting, routing
      ↓
AI Orchestrator   — LangGraph agents · LiteLLM gateway
      ↓
Workflow Engine   — Temporal (durable, human-in-the-loop)
      ↓
Knowledge Layer   — LlamaIndex RAG · Docling parsing · pgvector
      ↓
Connector Hub     — MCP registry + gateway · Composio
      ↓
Enterprise Systems  — Microsoft · Google · CRM · ERP · SharePoint …

Cross-cutting: Keycloak · Cerbos · Audit log · Langfuse/OTel · MinIO · Postgres · Redis · NATS
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the [ADRs](docs/adr) for the full picture and every build-vs-buy decision.

## Repo layout

| Path                      | What                                                                                               |
| ------------------------- | -------------------------------------------------------------------------------------------------- |
| `services/gateway`      | Public ingress: JWT auth, tenant context, rate limit, REST + GraphQL, routing                      |
| `services/identity`     | Keycloak integration; Organizations → tenants; user/role sync                                     |
| `services/authz`        | Cerbos client + policies;`check(principal, action, resource)`                                    |
| `services/orchestrator` | LangGraph agents + LiteLLM; streaming chat; Langfuse traces                                        |
| `services/workflows`    | Temporal worker + generic document-review-approval workflow                                        |
| `services/knowledge`    | Upload → MinIO → Docling → chunk → embed → pgvector; RAG retrieval                            |
| `services/connectors`   | Connector Hub: MCP registry + gateway + Composio                                                   |
| `services/audit`        | Append-only, tenant-scoped audit log + query API                                                   |
| `services/admin`        | Admin API: tenants, users, roles, connectors, audit, health                                        |
| `packages/shared`       | The reuse spine: tenant context, auth, DB+RLS, telemetry, Cerbos client, audit emitter, LLM config |
| `deploy`                | `docker-compose.yml` (full stack), Helm charts, seed, Keycloak/Cerbos config                     |
| `docs`                  | `ARCHITECTURE.md`, `DEPLOYMENT.md`, `MULTI_TENANCY.md`, `adr/`                             |

## Quick start (on-prem / dev baseline)

```bash
cp .env.example .env
make up          # brings up the ENTIRE stack (infra + all services) with health checks + seed
make health      # verify every service reports healthy
make seed        # (re)run seed: one demo tenant + owner/admin/member/viewer users
make smoke       # run smoke tests
make down        # stop everything
```

`docker-compose up` on a fresh machine is the supported on-prem baseline. Helm charts in
[`deploy/helm`](deploy/helm) mirror the compose stack for cloud/K8s.

## Definition of Done (Milestone 1)

A seeded tenant can: **log in via Keycloak** → **open a streamed chat (traced in Langfuse)**
→ **upload a document (parsed, embedded, retrievable via RAG)** → **run the generic approval
workflow (Temporal, human approve step)** → **see every action in the audit log** —
all enforced by Cerbos and scoped to the tenant. OpenAPI + GraphQL schema are published
for the frontend track.

## Tech (all decided — see ADRs)

Keycloak · Cerbos · FastAPI · LangGraph · LiteLLM · Temporal · LlamaIndex · pgvector (→ Qdrant) ·
Docling · MinIO · PostgreSQL · Redis/Valkey · NATS · MCP + Composio · Langfuse + OpenTelemetry · Infisical · Resend
