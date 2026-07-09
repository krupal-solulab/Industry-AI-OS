# ADR-0000: Multi-tenancy isolation model

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** **Build** (thin) — single Keycloak realm + Organizations for tenant
  identity, shared PostgreSQL with row-level `tenant_id` + RLS for tenant data.

## Context

The platform must be multi-tenant from day one, support on-prem/cloud/SaaS, and let a
tenant later be promoted to stricter isolation without a rewrite. This is the one
genuinely custom design decision in Milestone 1 — everything else is a tool choice.
Two axes: (a) how tenants map onto identity, (b) how tenant data is isolated.

## Options considered

**Identity mapping**
- Realm-per-tenant — strong SSO/branding isolation, heavier realm operations.
- **Single realm + Organizations** — tenants are orgs in one realm; simpler ops,
  easy cross-tenant admin, adequate grouping of users/roles. *(chosen)*

**Data isolation**
- Separate database per tenant — strongest, most expensive/ops-heavy.
- Schema per tenant — strong, migration/ops overhead up front.
- **Shared DB + `tenant_id` + PostgreSQL RLS** — cheapest to run and seed, DB-enforced,
  promotable later. *(chosen)*

## Decision

Single realm with Keycloak **Organizations** = tenants; shared Postgres with
`tenant_id` on every tenant-owned table and **RLS** as the last line of defense.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Thin build over bought primitives (Keycloak orgs, Postgres RLS). No custom auth or datastore. |
| Complexity | Low: one realm, one DB, one `tenant_id` convention. |
| Effort | Low now; promotion to schema/DB isolation is deferred and localized to the shared `db` layer. |
| Scalability | Good for many small/medium tenants; heavy tenants can be promoted individually. |
| Lock-in risk | Low: standard OIDC + standard Postgres features. |
| Cost | Lowest — no per-tenant infra. |
| Community maturity | Very high: Keycloak Organizations GA, Postgres RLS battle-tested. |

## Consequences

- Every tenant-owned table carries `tenant_id`; RLS policies filter by
  `current_setting('app.tenant_id')`. The app DB role holds no `BYPASSRLS`.
- Tenant context is minted **only** by the gateway from a verified JWT and passed
  downstream as a signed header; no service trusts a client-supplied tenant id.
- All data access flows through `packages/shared/db`, so switching a tenant to
  schema/DB isolation changes that layer only — callers are unaffected.
- Trade-off accepted: a noisy/huge tenant shares the DB until promoted; monitored
  via per-tenant metrics (OTel) so promotion is a data-driven decision.
