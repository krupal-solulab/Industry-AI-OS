# ADR-0018: Nango as an authenticated REST-proxy connector kind (sandbox-first)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Decision:** Add **Nango** as a Connector Hub *kind* — a thin, generic, authenticated
  **REST proxy** to each provider's own API — and ship a **sandbox mode** so workflows run
  end to end with no accounts, swapping to live Nango via credentials only.

## Context

The accounting invoice workflow (and future industry flows) call SaaS providers — Gmail,
QuickBooks/Xero, Slack, Drive. Those providers already expose REST APIs (`GET /vendor`,
`POST /bill`, `chat.postMessage`). **Nango does not create business APIs** like
`searchVendor()`/`createBill()`; it provides OAuth, token refresh, and an authenticated
**proxy** to the provider's own endpoints. The AI OS supplies the intelligence
(validation, duplicate detection, tax, approval) *around* those calls — ~70–80% of the
code — while ~20–30% is simply invoking existing provider APIs through Nango.

## Decision

- A `NangoConnector` is a **generic pass-through**: `invoke(<HTTP method>, {endpoint,
  query, body}, config)` performs `<method> {nango_host}/proxy{endpoint}` with
  `Provider-Config-Key` + `Connection-Id`, and Nango injects auth. One class, one instance
  per provider (`nango.gmail`, `nango.quickbooks`, …). We write **no per-provider business
  methods**; workflow `connector.call` steps carry the method + endpoint.
- **Sandbox-first:** with no `NANGO_SECRET_KEY`/connection id, the connector returns
  **provider-shaped fixtures flagged `_sandbox: true`** (never presented as live). This
  lets the whole invoice workflow demo end to end with zero external accounts.
- **Nango-compatible from day one:** going live is a credentials change
  (`NANGO_SECRET_KEY` + per-tenant `connection_id`) — the **same connector, the same
  workflow definition, no code change**. Only response field-mapping may be added when
  real provider payloads differ from the sandbox shapes.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy the hard part (OAuth/token refresh/proxy = Nango); build only the thin invoke + our business logic. |
| Complexity | Low: one generic proxy connector; no per-provider SDKs. |
| Effort | Minimal per provider (add an instance + optional sandbox fixture). |
| Scalability | High: any Nango-supported provider is reachable through the same interface. |
| Lock-in risk | Moderate/low: providers' REST APIs are called directly through the proxy; Nango is swappable behind the `Connector` interface (ADR-0012). |
| Cost | Nango OSS/self-host or SaaS; sandbox has zero cost. |
| Community maturity | Nango is a maintained OSS unified-auth layer. |

## Consequences

- Complements ADR-0012 (connectors behind an interface): Nango joins Echo/Graph/Composio
  as a connector kind; callers never learn the backend.
- **Never fabricate as live:** sandbox responses are explicitly `_sandbox: true`; live
  calls return `{status: "ok", data}`; failures return `{status: "error"}`.
- The invoice-verification pack definition is written in the proxy style
  (`tool` = HTTP method, `arguments` = `{endpoint, query/body}`) so it is unchanged when
  moving from sandbox to live.
- Next: OAuth connection provisioning per tenant + response field-mapping for live QB/Gmail.
