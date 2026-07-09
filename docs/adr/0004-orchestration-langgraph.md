# ADR-0004: Agent orchestration

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — LangGraph

## Context

The platform runs multi-step, multi-agent AI workflows that need explicit state, branching,
loops, and human-in-the-loop pauses (approvals, corrections) rather than one-shot prompt
chains. We need an orchestration layer that models agents as a graph with durable,
inspectable state and clean interruption points, and that stays in the Python AI ecosystem
(ADR-0003). Ad-hoc orchestration glue becomes unmaintainable as agent count grows.

## Options considered

- **CrewAI** — ergonomic role-based agents, but opinionated and less explicit about state
  and control flow; harder to model arbitrary graphs and interrupts.
- **AutoGen** — strong conversational multi-agent patterns, but conversation-centric rather
  than graph/state-centric, and heavier to embed as a library.
- **Raw LangChain** — usable, but chains lack first-class stateful graphs, checkpointing,
  and structured human-in-the-loop; we'd rebuild those ourselves.
- **Build custom** — a bespoke state machine for agents is real engineering we'd have to
  maintain forever; explicitly avoided.
- **LangGraph** — stateful multi-agent graphs, checkpointing, and native human-in-the-loop
  interrupts, in Python. *(chosen)*

## Decision

LangGraph for agent orchestration, used inside `services/orchestrator`, modeling workflows
as stateful graphs with checkpointed state and explicit human-in-the-loop nodes.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom agent state machine. |
| Complexity | Moderate: graph model has a learning curve but keeps control flow explicit. |
| Effort | Low-moderate: state, checkpointing, and interrupts come built in. |
| Scalability | Good: graphs run per-request; checkpoint store backs longer-running graphs. |
| Lock-in risk | Moderate, contained: graph logic is domain code confined to `services/orchestrator`. |
| Cost | Free OSS. |
| Community maturity | Good and fast-moving; strong adoption in the agent ecosystem. |

## Consequences

- Multi-agent logic lives in `services/orchestrator` as LangGraph graphs; state and
  human-in-the-loop checkpoints are first-class, not improvised.
- Long-running, durable business processes (as opposed to in-request agent reasoning) are
  Temporal's job (ADR-0006); LangGraph handles the agent graph, Temporal the durable
  workflow around it.
- Swap path: orchestration is isolated to `services/orchestrator`, so replacing LangGraph
  with CrewAI/AutoGen or a custom engine is scoped to that service; other services call it
  over its API and are unaffected.
- Trade-off accepted: LangGraph is younger and evolving faster than our infra choices, so
  we pin versions and keep graph definitions decoupled from the rest of the platform.
