# Multi-tenancy & Security Model

## Isolation model (Milestone 1)

- **Identity:** a **single Keycloak realm** using **Organizations** to represent
  tenants. A user's org membership yields their `tenant_id`. This keeps realm
  operations simple while giving per-tenant user/role grouping.
- **Data:** **shared PostgreSQL** with a row-level `tenant_id` column on every
  tenant-owned table, protected by **PostgreSQL Row-Level Security (RLS)**.
- **Promotion path:** the design allows a tenant to later be moved to schema-level
  or database-level isolation **without changing application code**, because all
  data access flows through the shared `db` session that scopes by tenant.

## Tenant context flow

```
JWT (Keycloak) ──▶ Gateway validates + extracts org/tenant + roles
                    │
                    ├─ builds TenantContext
                    ├─ signs X-AIOS-Context (HMAC, INTERNAL_CONTEXT_SECRET)
                    ▼
              downstream service verifies signature, rebuilds TenantContext
                    │
                    ├─ opens DB session ─▶ SET LOCAL app.tenant_id = <tenant>
                    │                       (RLS policies filter every row)
                    └─ calls Cerbos check(principal, action, resource) before acting
```

**No service ever trusts a client-supplied tenant id.** The only authority is the
signed context minted by the gateway from the verified JWT.

## RLS enforcement

Each tenant-owned table has a policy of the form:

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t>
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
```

The application role is **not** a superuser and does **not** hold `BYPASSRLS`, so a
missing `WHERE tenant_id = …` in application code cannot leak data — the database
still filters.

## Authorization

Every mutating **and** reading endpoint calls Cerbos through the shared
`check(principal, action, resource)` interface. Starter roles: `owner`, `admin`,
`member`, `viewer`. Policies are versioned files under
[`deploy/cerbos/policies`](../deploy/cerbos/policies) — the application stays policy-free.

## Secrets

No secrets in code or committed env files. `.env.example` documents every variable;
real environments pull secrets from **Infisical** (or Vault). Local dev uses `.env`
(git-ignored).

## Audit

Every approval, configuration change, and connector call is recorded append-only
with `actor`, `tenant_id`, `timestamp`, `action`, and `before`/`after` snapshots.
