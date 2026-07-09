# Architecture Decision Records

Each ADR scores a decision on **seven criteria**: Build vs Buy, Complexity, Effort,
Scalability, Lock-in risk, Cost, and Community maturity. Default posture is
**reuse mature OSS behind a clean interface**; a custom build must argue its case.

| ADR | Decision | Choice |
|---|---|---|
| [0000](0000-multi-tenancy-isolation.md) | Multi-tenancy isolation model | Single realm + Organizations + shared-DB RLS |
| [0001](0001-identity-keycloak.md) | Identity & SSO | Keycloak |
| [0002](0002-authorization-cerbos.md) | Authorization (RBAC/ABAC) | Cerbos (fallback OPA) |
| [0003](0003-api-fastapi.md) | API / backend framework | FastAPI |
| [0004](0004-orchestration-langgraph.md) | Agent orchestration | LangGraph |
| [0005](0005-llm-gateway-litellm.md) | LLM gateway / routing | LiteLLM |
| [0006](0006-workflows-temporal.md) | Durable workflows | Temporal |
| [0007](0007-rag-llamaindex.md) | RAG framework | LlamaIndex |
| [0008](0008-vector-store-pgvector.md) | Vector store | pgvector → Qdrant at scale |
| [0009](0009-document-parsing-docling.md) | Document parsing | Docling (→ Unstructured) |
| [0010](0010-object-storage-minio.md) | Object storage | MinIO (S3 API) |
| [0011](0011-eventing-nats.md) | Eventing / queue | NATS (fallback RabbitMQ) |
| [0012](0012-connectors-mcp-composio.md) | Connectors | MCP + Composio |
| [0013](0013-observability-langfuse-otel.md) | Observability | Langfuse + OpenTelemetry |
| [0014](0014-secrets-infisical.md) | Secrets | Infisical (or Vault) |
| [0015](0015-workflow-pack-framework.md) | Workflow Pack Framework (M2) | Declarative workflow definitions over a generic engine |

See [_template.md](_template.md) for the format.
