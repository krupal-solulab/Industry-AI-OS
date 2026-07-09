# ADR-0011: Eventing / queue

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — NATS (RabbitMQ as fallback)

## Context

Services need asynchronous messaging — publishing domain events, decoupling producers from
consumers, and lightweight work distribution. Durable, long-running orchestration is
Temporal's job (ADR-0006); this decision is about the fast, lightweight event/message bus
underneath. It must run on-prem with a small operational footprint and not drag in a heavy
distributed-log platform we don't need yet.

## Options considered

- **RabbitMQ** — mature, feature-rich broker; kept as the fallback, but heavier to operate
  and more moving parts than NATS for our needs.
- **Kafka** — the standard for high-throughput event streaming, but operationally heavy
  (brokers, ZooKeeper/KRaft, partitions) and overkill for current volumes.
- **Redis Streams** — simple and often already present, but weaker delivery guarantees and
  a less complete messaging model than a purpose-built bus.
- **Build custom** — a message broker is core infrastructure we won't reinvent.
- **NATS (with JetStream)** — lightweight, fast, on-prem friendly, with optional persistence
  when durability is needed. *(chosen)*

## Decision

NATS as the eventing/messaging backbone, with JetStream for persistence where required, and
RabbitMQ documented as the fallback if richer broker semantics become necessary.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom broker. |
| Complexity | Very low: single lightweight binary, simple to run on-prem. |
| Effort | Low: minimal client integration; subjects and streams are straightforward. |
| Scalability | High: NATS clustering handles very high message rates. |
| Lock-in risk | Low: simple pub/sub semantics; RabbitMQ fallback proves portability. |
| Cost | Free OSS; tiny footprint. |
| Community maturity | High: CNCF project, production-proven. |

## Consequences

- Services publish and subscribe to events over NATS for asynchronous decoupling; heavy,
  durable multi-step processes still belong to Temporal (ADR-0006), keeping responsibilities
  clean.
- On-prem footprint stays small — NATS is a single lightweight component, not a Kafka-scale
  platform.
- Swap path: messaging is used through a thin publish/subscribe abstraction, so moving to
  RabbitMQ (the fallback) or Kafka if volume ever demands it is contained to that layer and
  deployment config; publishers and subscribers are unaffected.
- Trade-off accepted: NATS offers fewer built-in broker features than RabbitMQ, acceptable
  given our lightweight needs and the documented fallback.
