# ADR-0013: Observability

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — Langfuse + OpenTelemetry

## Context

The platform needs two related but distinct kinds of observability: LLM-specific
observability (prompt/response traces, token cost, latency, evaluations) and general
service observability (distributed traces, metrics, logs across services). We want both
on-prem-capable, low-lock-in, and wired once so services emit telemetry uniformly. No
single tool does both well, so the decision pairs a specialist with a standard.

## Options considered

- **LangSmith** — excellent LLM tracing, but a SaaS with lock-in and no self-host path;
  disqualified as primary.
- **Helicone** — LLM observability via a proxy; adds a hop and leans SaaS.
- **Phoenix (Arize)** — strong open LLM eval/tracing; considered, but Langfuse fits our
  self-host + cost/eval needs better.
- **Jaeger / Grafana (Tempo/Prometheus)** — the standard for service traces/metrics, fed by
  OpenTelemetry — complementary, not competing, with the LLM layer.
- **Build custom** — reinventing tracing/metrics pipelines is undifferentiated work.
- **Langfuse + OpenTelemetry** — Langfuse (self-hosted) for LLM traces/cost/eval; OTel as
  the vendor-neutral standard for service telemetry. *(chosen)*

## Decision

Langfuse for LLM observability and OpenTelemetry for service traces/metrics, both wired
once through the shared telemetry module and exported to self-hosted backends.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse both; no custom telemetry pipeline. |
| Complexity | Moderate: two systems, but each targets a distinct, well-scoped concern. |
| Effort | Low: instrumentation is centralized in one shared module. |
| Scalability | High: OTel is built for scale; Langfuse self-hosts to platform volumes. |
| Lock-in risk | Low: OTel is a vendor-neutral standard; Langfuse is OSS and self-hostable. |
| Cost | Free OSS self-hosted; only backend infra. |
| Community maturity | Very high for OTel; high and rising for Langfuse. |

## Consequences

- Services instrument through `ai_os_shared/telemetry.py`: LLM calls (via the gateway,
  ADR-0005) flow to Langfuse with cost and eval data; service traces/metrics flow through
  OTel to a backend (e.g. Grafana/Jaeger). Per-tenant cost and usage metrics enable the
  data-driven promotion decisions in ADR-0000 and ADR-0008.
- Everything self-hosts, so on-prem observability is intact with no SaaS in the path.
- Swap path: because all instrumentation is centralized in `telemetry.py` and OTel is a
  neutral standard, replacing Langfuse (with Phoenix) or the OTel backend (Jaeger, Tempo,
  a vendor) is confined to that module plus exporter config; service code is unaffected.
- Trade-off accepted: two observability systems to operate, justified because LLM cost/eval
  and service tracing are genuinely different problems each better served by a specialist.
