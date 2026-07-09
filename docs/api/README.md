# API surface (for the frontend / Replit track)

The frontend talks to the **gateway only** (`http://localhost:8000` in dev; the
frontend dev server itself uses :8080). It never calls a service or an enterprise
system directly. Both REST and GraphQL are offered.

## Authentication

1. In production the frontend runs the standard OIDC flow against Keycloak
   (`realm: industry-ai-os`, client `aios-gateway`) and sends the resulting access
   token as `Authorization: Bearer <token>`.
2. For local dev / tests, the gateway exposes a convenience endpoint:

   ```
   POST /auth/token?username=owner@demo.aios.local&password=Passw0rd!
   ```

   Demo users (all password `Passw0rd!`): `owner@`, `admin@`, `member@`, `viewer@demo.aios.local`.

## REST (reverse-proxied through the gateway)

All service routes are namespaced under `/api/{service}/...`:

| Capability | Method + path |
|---|---|
| Current user | `GET /api/identity/me` |
| Chat (non-stream) | `POST /api/orchestrator/chat` `{message, session_id?, use_rag?}` |
| Chat (SSE stream) | `POST /api/orchestrator/chat/stream` |
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

## GraphQL

Endpoint: `POST http://localhost:8000/graphql` (GraphiQL playground on `GET`).

Queries: `me`, `auditEvents(limit)`, `workflows`.
Mutations: `chat(message, sessionId, useRag)`, `startDocumentReview(documentId)`,
`approveWorkflow(workflowId, comment)`, `rejectWorkflow(workflowId, comment)`.

The committed SDL is in [`graphql.schema.graphql`](graphql.schema.graphql).

## Rate limiting

The gateway enforces a per-tenant+user limit (default 120/min); responses carry
`X-RateLimit-Remaining`. A `429` includes `Retry-After`.
