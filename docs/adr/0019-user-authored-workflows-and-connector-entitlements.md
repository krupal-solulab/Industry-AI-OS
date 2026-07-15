# ADR-0019 — User-authored workflows (visual builder) + connector entitlements

- Status: Accepted
- Date: 2026-07-14
- Supersedes/relates: ADR-0015 (workflow pack framework), ADR-0018 (connectors / Nango)

## Context

Two customer-facing needs:

1. **Users want to build their own flows** — an n8n-style canvas where a user picks
   connectors, wires steps, names the flow, saves it, and then the AI assistant can run it
   ("find my mail with workflow1"). Alongside these, we keep shipping **fixed flows** (the
   seeded packs, e.g. invoice verification).
2. **Per-tenant connector access** — some tenants get the whole catalogue, others only the
   two connectors they asked for. Today `GET /connectors` returns the entire global
   registry to every tenant; the only per-tenant state is an enable/disable toggle.

The existing workflow engine (ADR-0015) runs a `WorkflowDefinition` as an **ordered list of
steps with `when` guards for branching** — not a free-form DAG. Definitions were loaded from
the repo pack files on disk; the `workflow_definitions` DB table was written by the seed job
but never read at runtime.

## Decision

### 1. User flows are `WorkflowDefinition`s stored in the DB, run by the same engine

- The visual builder serializes to the **same validated `WorkflowDefinition`** schema as
  shipped packs. No new execution model: the canvas is constrained to a **linear pipeline
  with conditional branches** (`branch` step + `when` guards), which is exactly what the
  engine already executes. A true parallel/merge DAG was rejected for now — it would require
  a new graph executor for marginal benefit over branch guards.
- User flows use the **reserved pack key `custom`** and carry `source='user'` (migration
  `0004` adds `source`, `created_by`, `updated_at` to `workflow_definitions`). Shipped packs
  are `source='seed'`.
- The runtime now resolves definitions **DB-first, disk-fallback** (`load_definition`): a
  `(tenant, pack, workflow)` row wins; otherwise the on-disk pack is loaded. This makes
  builder flows runnable through the identical generic engine while shipped packs keep
  working unchanged.
- CRUD lives on the workflows service: `POST /packs/definitions`,
  `PUT /packs/definitions/{key}`, `DELETE /packs/definitions/{key}`. Create is an upsert
  keyed by the definition's `key`; update/delete are **guarded to `source='user'`** so a
  seeded flow can never be mutated or clobbered.
- `GET /packs/definitions` returns seeded **and** user flows in one graph-spec list, each
  tagged with `source` and its latest run status.

### 2. The assistant sees a tenant's saved flows

`classify_intent` accepts `extra_workflow_keys`; the orchestrator fetches
`/packs/definitions` before classifying, feeds all workflow keys into the classifier prompt,
and starts a resolved run with the **correct `pack_key`** (user flow → `custom`) via a
`{workflow_key: pack_key}` map. A definitions-lookup failure degrades gracefully (empty
extras, default pack) — chat never breaks because the lookup failed.

### 3. Connector entitlements = per-tenant opt-in allowlist

- New RLS-scoped table `connector_entitlements(tenant_id, connector_key, allowed, …)`,
  unique `(tenant_id, connector_key)`.
- **Unrestricted until restricted**: a tenant with **no** entitlement rows can use the whole
  catalogue (so the Connector Hub is never empty out of the box). The moment an admin grants
  it any connector, the tenant becomes *restricted* — an allowlist — and only `allowed=true`
  connectors are visible/usable. The `echo` reference connector is always usable. (This
  replaced the original strict opt-in, which surprised users with an empty Hub; per-tenant
  curation is preserved — granting is how you scope a tenant down.)
- `GET /connectors` filters to entitled connectors by default; `?all=true` returns the full
  catalogue each with an `entitled` flag (for an admin/builder palette). `invoke` and
  `configure` (when enabling) enforce entitlement.
- Management: `GET /connectors/entitlements`, `PUT /connectors/entitlements/{key}`
  (`{allowed}`), and `POST /connectors/entitlements/grant-defaults` (grants every
  non-reference connector to the tenant — the one-time grandfather for existing tenants).

### 4. Connector access requests ("Pending Permissions")

For a *restricted* tenant, the FE Connector Hub splits into **Connected Systems** (enabled),
**Available Connectors** (entitled, not yet connected), and **Pending Permissions** (not
entitled). A member requests a not-entitled connector; an owner/admin approves or rejects.

- New RLS-scoped table `connector_access_requests(tenant_id, connector_key, status
  [pending|approved|rejected], requested_by, decided_by, …)` — migration `0005`; a partial
  unique index caps it at one *pending* request per (tenant, connector).
- `POST /connectors/{key}/request-access` (member; 422 if already available),
  `GET /connectors/access-requests?status=`, `POST /connectors/access-requests/{id}/approve`
  and `…/reject` (owner/admin only). **Approve grants the entitlement** (inserts the
  `connector_entitlements` row) in the same transaction that marks the request approved.
- Unrestricted tenants have everything entitled, so Pending Permissions is empty for them —
  the flow only surfaces once a tenant is scoped down.

## Consequences

- Adding a user flow is a data write, not a deploy — same posture as ADR-0016 (industry =
  config). The closed step vocabulary (ADR-0015) still bounds what a flow can do, so a
  user-built flow is as safe/auditable as a shipped one.
- **No empty-Hub surprise**: with the unrestricted-until-restricted model, tenants (incl. the
  demo) see the full catalogue by default — nothing to grandfather. Scoping a tenant down is a
  deliberate admin action (grant it a subset). `grant-defaults` remains available but is no
  longer required for anything to appear.
- Entitlement management (`PUT /connectors/entitlements/{key}`, `grant-defaults`) is
  **owner/admin-only** — an in-code `ctx.has_role(OWNER, ADMIN)` guard (403 otherwise) on top
  of the resource-level `check_ctx`; reading the list stays open to any member. The role gate
  is in-code (no Cerbos policy change). Moving management to the admin service (cross-tenant,
  central) remains a possible future refinement but is not required.
- Migration `0004` only creates schema; it cannot seed entitlement rows because these tables
  `FORCE` RLS and a migration has no tenant context.

## Activation (operational)

1. `alembic upgrade head` (applies `0004`).
2. Connectors are visible by default (unrestricted) — no grant step needed. To *scope* a
   tenant, grant it a subset via `PUT /api/connectors/connectors/entitlements/{key}`.
3. `POST /api/workflows/packs/seed` still seeds the shipped packs as before.
4. Build/save a flow in the builder → it persists under pack `custom` and becomes runnable
   and assistant-addressable immediately.
