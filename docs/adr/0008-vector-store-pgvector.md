# ADR-0008: Vector store

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — pgvector now, Qdrant at scale

## Context

RAG (ADR-0007) needs vector similarity search over embeddings. The platform already runs
PostgreSQL (ADR-0000) for tenant data. Adding a dedicated vector database on day one means
new infrastructure to deploy, secure, back up, and operate per environment — cost we don't
want until scale justifies it. We need a choice that starts with zero new infra but can be
promoted to a purpose-built store when volume or latency demands, without rewriting callers.

## Options considered

- **Qdrant** — excellent purpose-built vector DB; the chosen target *at scale*, but new
  infra we don't need on day one.
- **Weaviate** — capable vector DB with modules, but heavier and again new infra up front.
- **Milvus** — high-scale vector DB, operationally heavy; overkill until we're much larger.
- **Pinecone** — managed and fast, but SaaS lock-in with no on-prem story; disqualified.
- **Build custom** — writing an ANN index is deep specialist work we won't own.
- **pgvector** — vector search inside the Postgres we already run: no new infra. *(chosen now)*

## Decision

pgvector on the existing PostgreSQL now; **Qdrant** as the pre-agreed target when scale
(index size or query latency) demands a dedicated store.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse a Postgres extension now; buy/reuse Qdrant later. |
| Complexity | Very low now: reuses existing DB, backups, and RLS. |
| Effort | Very low now: an extension, not a new system. |
| Scalability | Adequate to mid-scale on pgvector; Qdrant covers high scale on promotion. |
| Lock-in risk | Low: standard SQL + a common extension; abstracted behind the DB layer. |
| Cost | Lowest now — no new infra; dedicated-store cost deferred until warranted. |
| Community maturity | Very high (Postgres + pgvector); Qdrant is mature for the scale phase. |

## Consequences

- Embeddings are stored and queried through the `services/knowledge` DB layer, which reuses
  existing Postgres operations (backups, RLS tenant isolation per ADR-0000) — no new
  infrastructure to run.
- Swap path: the knowledge service's DB layer is the only code that speaks to the vector
  store, so promoting a tenant (or the whole platform) to Qdrant is a change confined to
  that layer; LlamaIndex and all callers are unaffected.
- Promotion is data-driven, decided on per-tenant vector volume and query-latency metrics
  (OTel, ADR-0013), matching the isolation-promotion philosophy of ADR-0000.
- Trade-off accepted: pgvector's ANN performance trails a dedicated store at high scale,
  acceptable because the swap is localized and deferred until metrics justify it.
