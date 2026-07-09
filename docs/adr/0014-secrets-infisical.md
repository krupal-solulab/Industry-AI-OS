# ADR-0014: Secrets management

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — Infisical (Vault as fallback)

## Context

The platform holds many secrets — provider API keys (ADR-0005), datastore and IdP
credentials, connector tokens (ADR-0012). They must be centralized, access-controlled,
auditable, and rotatable, never committed to source or baked into images. It must run
on-prem with no dependency on a cloud secrets service, while keeping local development
frictionless.

## Options considered

- **HashiCorp Vault** — the mature, powerful standard; kept as the fallback, but heavier to
  operate than we need for straightforward secret storage and sync.
- **AWS Secrets Manager** — managed and solid, but cloud lock-in with no on-prem story;
  disqualified as primary.
- **SOPS** — encrypts secrets in git; good for GitOps, but file-based rather than a
  centralized, access-controlled, auditable service with rotation.
- **Plain env vars / .env everywhere** — fine for local dev, unacceptable for real
  environments (no access control, audit, or rotation).
- **Build custom** — a secrets store is security-critical infrastructure we won't reinvent.
- **Infisical** — self-hostable secret management with access control, audit, and env sync,
  and a good developer workflow. *(chosen)*

## Decision

Infisical as the secrets manager for real environments, with local dev using `.env` and
real environments pulling from Infisical; Vault documented as the fallback.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom secrets store. |
| Complexity | Low-moderate: simpler to run than Vault, adequate for our needs. |
| Effort | Low: env sync and SDKs make integration straightforward. |
| Scalability | Good: covers platform secret volumes; self-hosted clustering available. |
| Lock-in risk | Low: standard secret/env model; Vault fallback proves portability. |
| Cost | Free OSS self-hosted. |
| Community maturity | Good and growing; Vault is the mature backstop if needed. |

## Consequences

- Local development reads a `.env` file for zero friction; every real environment pulls
  secrets from Infisical at deploy/runtime, so credentials never live in source or images.
- On-prem stays possible because Infisical self-hosts with no cloud dependency.
- Swap path: secret access is abstracted at the configuration/loading layer (the app factory
  wiring in ADR-0003 resolves config from the secret source), so replacing Infisical with
  Vault (the fallback) or another provider is confined to that loader and deployment config;
  service code, which just reads resolved config, is unaffected.
- Trade-off accepted: we operate a secrets service ourselves rather than using a managed one,
  the necessary cost of the on-prem requirement, with Vault available if we outgrow Infisical.
