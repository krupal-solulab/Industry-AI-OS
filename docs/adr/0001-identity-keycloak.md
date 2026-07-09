# ADR-0001: Identity & SSO

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — Keycloak

## Context

The platform needs authentication, SSO (OIDC/SAML), and a user/identity store that
works on-prem, in cloud, and in SaaS without changing the app. Multi-tenancy (see
ADR-0000) maps tenants onto identity, so the identity system must model tenants
cleanly and stay standards-based to keep lock-in near zero. Rolling our own auth is a
security and maintenance liability we explicitly want to avoid.

## Options considered

- **Auth0 / Okta** — mature SaaS, fastest to start, but a hosted dependency with pricing
  that scales per-user and no clean on-prem story; hard vendor lock-in.
- **Authentik** — solid self-hosted IdP, smaller ecosystem, fewer enterprise SSO edge cases proven.
- **Ory (Kratos/Hydra)** — composable and modern, but assembly-required across several
  services; more integration burden than we want in Milestone 1.
- **Build custom** — full control, but re-implementing OIDC, token handling, and user
  management is exactly the undifferentiated risk we refuse to own.
- **Keycloak** — self-hostable, standards-first (OIDC/SAML), Organizations feature maps
  directly to tenants, huge community. *(chosen)*

## Decision

Keycloak, run as a single realm with **Organizations = tenants**, issuing standard OIDC
tokens the platform verifies at the gateway.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse mature OSS; zero custom auth code. |
| Complexity | Moderate: one realm to operate, but well-documented and containerized. |
| Effort | Low integration effort behind standard OIDC libraries. |
| Scalability | High: clustered Keycloak handles large user counts; realm stays single. |
| Lock-in risk | Low: pure OIDC/SAML; any compliant IdP can replace it. |
| Cost | Free OSS; only the infra it runs on. |
| Community maturity | Very high: CNCF-adjacent, enterprise-proven, long track record. |

## Consequences

- All services trust only verified OIDC tokens; tenant identity comes from the
  Organization claim, consumed by the gateway per ADR-0000.
- On-prem stays possible because Keycloak self-hosts with no external dependency.
- Swap path: because callers only ever touch `ai_os_shared/auth.py` (token verification,
  current-user/current-tenant resolution) and never Keycloak APIs directly, moving to
  Authentik, Ory, or a hosted OIDC provider is a change confined to that module plus
  deployment config — no service code changes.
- Trade-off accepted: we operate an IdP ourselves rather than outsourcing it, in exchange
  for on-prem capability and no per-user SaaS billing.
