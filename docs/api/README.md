# API surface (for the frontend / Replit track)

The frontend talks to the **gateway only** (`http://localhost:8000` in dev; the
frontend dev server itself uses :8080). It never calls a service or an enterprise
system directly. Both REST and GraphQL are offered.

## Authentication

1. In production the frontend runs the standard OIDC flow against Keycloak
   (`realm: industry-ai-os`, client `aios-gateway`) and sends the resulting access
   token as `Authorization: Bearer <token>`.
2. For local dev / tests, the gateway exposes convenience endpoints (credentials in the
   JSON **body**, never the URL):

   ```
   POST /auth/token     {"username": "owner@demo.aios.local", "password": "Passw0rd!"}
   POST /auth/register  {"email","password","first_name","last_name","login_source"}
   ```

   `/auth/register` is public self-service signup: it creates the user (Keycloak + a
   `user_profiles` row with the chosen `login_source` industry) and returns a token so the
   user is logged straight in. Demo users (all password `Passw0rd!`): `owner@`, `admin@`,
   `member@`, `viewer@demo.aios.local`.

## REST (reverse-proxied through the gateway)

All service routes are namespaced under `/api/{service}/...`:

| Capability | Method + path |
|---|---|
| Industries (public, pre-login) | `GET /industries` → `[{key, name, tagline, theme}]` |
| Current user | `GET /api/identity/me` (returns `role`, `login_source`) |
| Workspace config (current user's industry) | `GET /api/identity/workspace/config` → `{login_source, industry, workspace:{display_name,theme,nav[],entities[],terminology,copilots[]}, workflow_packs[]}` |
| Chat (non-stream) | `POST /api/orchestrator/chat` `{message, session_id?, use_rag?, workspace?}` → `{answer, session_id, model, intent, workspace}` |
| Chat (SSE stream) | `POST /api/orchestrator/chat/stream` `{…, workspace?}` — frames: `{session_id,model}`, `{delta}`, `{error}` (on LLM failure), then `[DONE]` |
| Upload document | `POST /api/knowledge/documents` (multipart `file`) |
| RAG retrieve | `POST /api/knowledge/retrieve` `{query, top_k}` |
| List documents | `GET /api/knowledge/documents` |
| Start approval workflow | `POST /api/workflows/document-review` `{document_id}` |
| Approve / reject | `POST /api/workflows/{id}/approve` · `/reject` `{comment}` |
| List / get workflows | `GET /api/workflows` · `/api/workflows/{id}` |
| List connectors | `GET /api/connectors` |
| Invoke connector tool | `POST /api/connectors/{key}/invoke` `{tool, arguments}` |
| Audit log | `GET /api/audit/events` |
| Admin: tenant, health | `GET /api/admin/tenant` · `/api/admin/system/health` |

**OpenAPI:** each service publishes its own spec at `/openapi.json` (and Swagger UI at
`/docs`). The gateway's own spec is at `http://localhost:8000/openapi.json`.

## AI Assistant (workspace-aware chat)

The chat endpoints are the conversational interface for the caller's **industry
workspace**. Pass `workspace` (e.g. `"accounting"`) — the FE is industry-specific so it
knows it; otherwise the backend falls back to the user's `login_source`. The assistant
detects intent (general / workspace / knowledge-search / workflow-execution /
document-analysis / workflow-status / approval-status / conversation) and pulls **real**
data from existing APIs (knowledge `/retrieve`, workflows list/status) — it never executes
workflows or invents data itself. Behavior is set by the backend `ASSISTANT_MODE` env:
`strict` | `strict_lenient` (default — answer anything, append a workspace reminder for
unrelated questions) | `lenient`. If no LLM key is configured, chat returns a clean error
(non-stream: `502`; stream: an `{error}` frame) — never a fake answer.

## GraphQL

Endpoint: `POST http://localhost:8000/graphql` (GraphiQL playground on `GET`).

Queries: `me`, `auditEvents(limit)`, `workflows`.
Mutations: `chat(message, sessionId, useRag)`, `startDocumentReview(documentId)`,
`approveWorkflow(workflowId, comment)`, `rejectWorkflow(workflowId, comment)`.

The committed SDL is in [`graphql.schema.graphql`](graphql.schema.graphql).

## Rate limiting

The gateway enforces a per-tenant+user limit (default 120/min); responses carry
`X-RateLimit-Remaining`. A `429` includes `Retry-After`.
