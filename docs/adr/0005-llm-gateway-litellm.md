# ADR-0005: LLM gateway / routing

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — LiteLLM

## Context

The platform calls LLMs from many services and must not be welded to a single provider.
We need one interface across providers, with routing, fallback, per-tenant cost tracking,
and rate limiting, so a provider outage or price change is a config change, not a code
change. It must run on-prem and keep provider credentials in one controlled place.

## Options considered

- **OpenRouter** — convenient hosted multi-provider gateway, but a SaaS dependency in the
  request path with lock-in and no on-prem story.
- **Direct provider SDKs** — simplest per call, but scatters provider-specific code
  everywhere and makes fallback/cost control a per-service reinvention.
- **Portkey** — capable gateway, but leans toward its hosted offering and adds a vendor
  relationship we'd rather not put in the critical path.
- **Build custom** — a homegrown routing/fallback layer is undifferentiated work we'd
  maintain indefinitely.
- **LiteLLM** — one OpenAI-compatible API across ~all providers, with routing, fallback,
  rate limits, and cost callbacks, self-hostable. *(chosen)*

## Decision

LiteLLM as the single LLM gateway, self-hosted, with **Claude as primary and OpenAI as
fallback**, accessed only through the shared LLM wrapper.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom multi-provider routing. |
| Complexity | Low: one OpenAI-compatible surface hides provider differences. |
| Effort | Low: providers, fallbacks, and budgets are configuration. |
| Scalability | High: stateless proxy scales horizontally; caching/rate-limits built in. |
| Lock-in risk | Low: OpenAI-compatible API is the de-facto standard; providers are swappable. |
| Cost | Free OSS; we pay only underlying provider usage, tracked per tenant. |
| Community maturity | High: widely adopted as the standard OSS LLM gateway. |

## Consequences

- All model calls go through `ai_os_shared/llm.py`; services never import a provider SDK,
  so switching or adding providers is central config.
- Claude-primary/OpenAI-fallback gives resilience against a single-provider outage; per-tenant
  cost and rate limits are enforced in one place and exported to telemetry (ADR-0013).
- Swap path: because the wrapper exposes a provider-neutral interface and LiteLLM itself
  speaks the OpenAI-compatible standard, replacing LiteLLM (with Portkey, OpenRouter, or a
  custom proxy) is confined to `ai_os_shared/llm.py`.
- Trade-off accepted: the gateway is one more service in the request path, justified by
  centralized fallback, cost control, and provider independence.
