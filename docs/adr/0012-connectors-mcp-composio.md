# ADR-0012: Connectors to third-party systems

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — MCP servers + Composio

## Context

Agents and workflows must reach hundreds of external systems — CRMs, ticketing, storage,
SaaS APIs. Hand-writing and maintaining a client per system (auth, pagination, rate limits,
schema drift) for 500 APIs is unsustainable. We want enterprise integration breadth without
owning that maintenance, exposed to agents through a standard tool interface (MCP), while
keeping all third-party access behind a single controllable boundary.

## Options considered

- **Hand-written API clients per system** — full control, but linear, unbounded maintenance
  cost that scales with every integration and every upstream change. Rejected as the default.
- **Zapier / Workato** — huge connector catalogs, but SaaS platforms in the integration path
  with lock-in and no on-prem story.
- **n8n** — self-hostable workflow/automation tool with many nodes, but oriented at visual
  workflows rather than an agent-native tool protocol; kept in mind but not primary.
- **Build custom** — see hand-written clients; the same unbounded burden.
- **MCP servers + Composio** — MCP gives agents a standard tool protocol; Composio supplies
  managed breadth across hundreds of apps with auth handled. *(chosen)*

## Decision

Expose integrations to agents as MCP tools, backed by Composio for connector breadth and
managed auth, all confined to the Connector Hub.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse breadth; we own only the hub boundary, not 500 clients. |
| Complexity | Moderate: MCP + Composio wiring, but far less than per-API clients. |
| Effort | Low relative to hand-writing connectors; auth and schemas are provided. |
| Scalability | High: adding an integration is configuration, not a new codebase. |
| Lock-in risk | Moderate, contained: MCP is an open protocol; Composio is isolated to the hub. |
| Cost | Composio usage cost, offset against the labor of maintaining hundreds of clients. |
| Community maturity | MCP is fast-emerging as a standard; Composio is maturing quickly. |

## Consequences

- The **Connector Hub** (`services/connectors`) is the *only* layer that touches third-party
  APIs; every other service reaches external systems through MCP tools it exposes, so
  credentials, rate limits, and auditing live in one place.
- Adding an integration is largely configuration in the hub rather than a new client library
  to build and maintain.
- Swap path: because external access is funneled through the hub's MCP tool interface,
  replacing Composio (with n8n, direct clients for a critical few, or another provider) is
  contained to `services/connectors`; agents and workflows are unaffected.
- Trade-off accepted: dependence on Composio's coverage and a maturing MCP ecosystem, bounded
  by isolating both behind the hub and retaining the option of hand-written clients for a
  critical few.
