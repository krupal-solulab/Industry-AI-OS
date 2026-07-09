# ADR-0002: Authorization (RBAC/ABAC)

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — Cerbos (OPA as fallback)

## Context

Authentication (ADR-0001) tells us *who* a caller is; we still need to decide *what*
they may do. The platform needs both role-based and attribute-based rules (tenant,
resource ownership, environment) evaluated consistently across every service, versioned
and auditable, and decoupled from application code so policy changes don't require
redeploys. It must run on-prem with no external policy SaaS.

## Options considered

- **OPA (Open Policy Agent)** — CNCF-graduated, extremely general, but Rego is a steep
  language and policies are lower-level than we need. Kept as the fallback.
- **OpenFGA** — relationship/Zanzibar-style; powerful for graph permissions but overkill
  and awkward for our RBAC+ABAC-with-attributes shape.
- **Casbin** — library, not a service; embeds in-process and drifts per-language, harder
  to centralize and version across polyglot services.
- **Build custom** — scattering `if role == ...` checks through services is exactly the
  un-auditable, un-versioned sprawl we want to eliminate.
- **Cerbos** — purpose-built policy decision service for RBAC/ABAC, policies as YAML
  files, stateless, self-hosted. *(chosen)*

## Decision

Cerbos as a stateless decision service; every access check goes through a single
`check(principal, action, resource)` call. OPA remains a documented fallback.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no hand-rolled permission logic in services. |
| Complexity | Low: YAML policies are readable; the service is stateless and simple to run. |
| Effort | Low: single `check()` interface; policies added as files, not code. |
| Scalability | High: stateless PDP scales horizontally; sidecar or central deployment. |
| Lock-in risk | Low: policies are portable YAML; OPA fallback proves the abstraction. |
| Cost | Free OSS; negligible runtime footprint. |
| Community maturity | Good and growing; Cerbos is production-proven, OPA is the mature backstop. |

## Consequences

- Policies live as versioned files in `deploy/cerbos/policies`, reviewed like code and
  audited via git history; changing a rule never touches application code.
- Every service authorizes through `ai_os_shared/authz.py` exposing
  `check(principal, action, resource)`; no service embeds its own rules.
- Swap path: because callers only see that one function, replacing Cerbos with OPA (or
  another PDP) means re-implementing `authz.py` against the new engine and porting
  policy files — service code is untouched.
- Trade-off accepted: a network hop per authorization decision, mitigated by co-locating
  the PDP and caching decisions where safe.
