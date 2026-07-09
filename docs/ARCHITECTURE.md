# Architecture — Industry AI OS (Milestone 1)

> Status: living document. Finalized alongside the platform build.
> This milestone delivers the reusable platform core only — **no industry code**.

## 1. Goal

One platform, many industries. 80–90% of code is shared; an industry is a
**configuration + workflow pack** loaded at runtime, never a fork. Milestone 1
proves the reusable spine end-to-end with a single generic workflow.

## 2. Layered model

The system is a stack of thin, independently deployable FastAPI services. Requests
enter only through the **Gateway**; each lower layer is reachable solely through the
layer above it or via the shared internal contract in [`packages/shared`](../packages/shared).

| Layer | Service | Reused OSS |
|---|---|---|
| Ingress | `gateway` | FastAPI, Strawberry (GraphQL) |
| Identity | `identity` | Keycloak |
| Authorization | `authz` | Cerbos |
| Orchestration | `orchestrator` | LangGraph, LiteLLM, Langfuse |
| Durable workflow | `workflows` | Temporal |
| Knowledge / RAG | `knowledge` | LlamaIndex, Docling, pgvector |
| Connectors | `connectors` | MCP, Composio |
| Audit | `audit` | (thin build on Postgres append-only) |
| Admin | `admin` | FastAPI |

Cross-cutting infra: PostgreSQL (+pgvector), Redis/Valkey, NATS, MinIO,
OpenTelemetry Collector, Infisical.

## 3. Request lifecycle

1. Client calls the **Gateway** (REST or GraphQL) with a Keycloak-issued JWT.
2. Gateway validates the JWT against Keycloak's JWKS, extracts the user and the
   **Organization membership** (= tenant).
3. Gateway resolves a `TenantContext` and signs it into an internal header
   (`X-AIOS-Context`, HMAC over tenant/user/roles). Downstream services trust only
   this signed header — never a client-supplied tenant id.
4. Before any read or write, the target service calls **Cerbos** via the shared
   `check(principal, action, resource)` interface.
5. Every state change and approval decision is emitted to the **Audit** service.
6. Every LLM call is traced in **Langfuse**; every service span is exported via OTel.

## 4. Multi-tenancy

Shared Postgres, row-level `tenant_id`, **RLS enforced**. Tenant context is set on
the DB session (`SET LOCAL app.tenant_id`) so the database — not application code —
is the last line of defense. Designed to be promotable to schema- or DB-level
isolation without changing callers. See [MULTI_TENANCY.md](MULTI_TENANCY.md) and
[ADR-000](adr/0000-multi-tenancy-isolation.md).

## 5. The reuse spine (`packages/shared`)

Every external dependency sits behind an interface here so it can be swapped:
`settings`, `tenant_context`, `auth` (JWT), `db` (async engine + RLS session),
`authz` (Cerbos client), `audit` (emitter), `llm` (LiteLLM config), `telemetry`
(OTel), `health`, `errors`, `types`.

## 6. Deployment

- **On-prem/dev:** `docker-compose up` brings up the entire stack with health checks
  and seed data on a fresh machine.
- **Cloud/K8s:** Helm charts mirror the compose stack.
- **SaaS:** same images; isolation model can be promoted per tenant.

See [DEPLOYMENT.md](DEPLOYMENT.md).

## 7. Decisions

Every major build-vs-buy decision has an ADR in [`docs/adr`](adr) scored on seven
criteria: build-vs-buy, complexity, effort, scalability, lock-in risk, cost, and
community maturity.
