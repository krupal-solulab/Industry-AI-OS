# ADR-0006: Durable workflows

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — Temporal

## Context

Beyond in-request agent reasoning (ADR-0004), the platform runs long-lived business
processes — multi-day ingestion jobs, approval flows, retries across flaky external
systems — that must survive process restarts and pick up exactly where they left off. We
need durable execution with automatic retries, timers, and signals for human approval,
running on-prem, without building our own saga/state-persistence machinery.

## Options considered

- **Celery** — a task queue, not a workflow engine; durable multi-step orchestration and
  signals must be hand-built on top.
- **Airflow** — batch/DAG scheduler oriented at data pipelines, not low-latency,
  signal-driven application workflows with human approval steps.
- **Prefect** — nicer than Airflow for dynamic flows, but still pipeline-oriented and
  weaker on durable, long-lived, signal-driven execution.
- **AWS Step Functions** — fully managed and durable, but cloud lock-in and no on-prem
  option; disqualified by the on-prem requirement.
- **Build custom** — a durable execution engine with retries and replay is a huge,
  error-prone undertaking we refuse to own.
- **Temporal** — durable execution, retries, timers, and signals as first-class,
  self-hostable. *(chosen)*

## Decision

Temporal for durable workflows, used in `services/workflows`, with signals driving
human-approval steps and its retry/timeout machinery replacing hand-rolled reliability code.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom durable-execution engine. |
| Complexity | Moderate: a server + workers to operate, but removes bespoke reliability code. |
| Effort | Low-moderate: workflows are ordinary Python; durability is provided. |
| Scalability | Very high: Temporal is built for millions of concurrent workflows. |
| Lock-in risk | Low-moderate: workflow code is portable Python; the engine is standard OSS, self-hostable. |
| Cost | Free OSS (self-hosted); Temporal Cloud optional but not required. |
| Community maturity | High: production-proven at large scale across many companies. |

## Consequences

- Durable, long-running processes live in `services/workflows` as Temporal workflows and
  activities; human approval is modeled as a signal, not a polling hack.
- Clear division of labor: LangGraph (ADR-0004) orchestrates agent reasoning within a
  step; Temporal guarantees the surrounding multi-step process is durable and resumable.
- Swap path: workflow definitions are Python confined to `services/workflows`, so moving to
  another engine (or Temporal Cloud) is scoped to that service and its worker deployment;
  callers trigger workflows over its API.
- Trade-off accepted: Temporal adds a server and worker fleet to operate, justified by
  eliminating an entire class of custom retry/state-persistence code.
