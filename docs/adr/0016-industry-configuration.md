# ADR-0016: Multi-industry frontends — industry is configuration, not code

- **Status:** Accepted
- **Date:** 2026-07-10
- **Decision:** Serve N industry-specific frontends from **one backend**, where an
  "industry" is **configuration** (a pack manifest's `workspace` block), not forked code.
  (Recorded as decision **D11** in PROJECT_MEMORY; "Plan A".)

## Context

The product is one platform specialized per line of business (Construction, Accounting,
Legal, …). Each industry gets its own landing page + workspace UI, but they share the
same APIs, auth, tenancy, and workflow engine. We needed the backend to (a) tell a
frontend which industry a user belongs to, (b) describe how to render that industry's
workspace, and (c) let us add an industry without touching platform code — consistent
with the 80–90%-reuse thesis (ADR-0015) and the existing `login_source` field.

The blocker: the industry list was a hardcoded Python set, and nothing exposed a
per-industry workspace description to a frontend.

## Options considered

1. **Fork a frontend/app per industry with hardcoded config** — fast, but industry logic
   and the industry list live in code; adding one is an engineering task.
2. **Industry as configuration over one backend (chosen)** — each `packs/<industry>/
   pack.json` carries an optional `workspace` block (display name, theme, nav, entities,
   terminology, copilots). A config-driven registry (`ai_os_shared.industry`) reads them;
   the gateway/identity expose `GET /industries` and `GET /api/identity/workspace/config`.
   Adding an industry = adding a `pack.json`.
3. **Hard-isolate each industry's data as a separate tenant (Plan B)** — cleaner data
   isolation, but a bigger migration and conflates "industry" with "organization".

## Decision

**Plan A:** industry differs the **interface + catalogue** (config-driven), while data
isolation stays at the existing tenant/RLS layer. The `workspace` block lives on the pack
manifest (co-located with that industry's workflow definitions). Signup validates
`login_source` against the registry (no hardcoded set). Plan B (per-industry data
isolation via separate tenants) is deferred until real multi-industry customers onboard.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Thin build: a manifest field + a small registry loader over existing packs. |
| Complexity | Low: read-only config; no new infra; one registry module. |
| Effort | Near-zero per new industry (drop a `pack.json`); frontends share one API contract. |
| Scalability | High: adding industries adds data, not load-bearing code or services. |
| Lock-in risk | Low: config is our own JSON; endpoints are plain REST. |
| Cost | None incremental. |
| Community maturity | N/A (internal config); depends on already-chosen primitives. |

## Consequences

- `PackManifest` gains an optional `workspace` block; the generic demo pack (industry
  `generic`) has none and is excluded from the industry list.
- New endpoints: public `GET /industries` (signup selectors, pre-login) and authed
  `GET /api/identity/workspace/config` (the caller's industry config).
- The packs directory is baked into every service image (`AIOS_PACKS_DIR=/app/packs`) so
  any service can resolve the registry at runtime.
- Frontends are industry-specific deployments that hit the same gateway; each reads
  `/workspace/config` (or is simply hard-scoped, as the Accounting FE is) to render nav,
  theme, and terminology. Data stays tenant-scoped by RLS — industry is a presentation +
  capability dimension, not an isolation boundary (until Plan B).
