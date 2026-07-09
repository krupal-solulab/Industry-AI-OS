# ADR-0003: API / backend framework

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — FastAPI

## Context

The platform needs a backend framework for its HTTP APIs across many services. The
decisive constraint is that the AI/RAG/agent ecosystem — LangGraph, LlamaIndex, LiteLLM,
the model SDKs — is Python-first. Choosing a non-Python stack would force a language
boundary between the API layer and the AI core, adding serialization overhead and a
second runtime to operate. We also want async I/O (LLM and vector calls are I/O-bound),
typed request/response models, and automatic OpenAPI.

## Options considered

- **Django REST Framework** — batteries-included, but heavyweight and ORM-centric for
  services that are mostly async AI calls, not CRUD over a relational model.
- **Flask** — minimal and familiar, but async support is bolted on and it lacks native
  typed validation and schema generation.
- **Node/NestJS** — excellent framework, but puts the API in a different language from the
  AI ecosystem, creating a costly boundary.
- **Go** — great for throughput, but the same language-boundary problem and far weaker AI
  library support.
- **Build custom** — no reason to reinvent an ASGI framework.
- **FastAPI** — async-native, Pydantic-typed, auto OpenAPI, and native to the Python AI
  stack. *(chosen)*

## Decision

FastAPI for all services, instantiated through one shared application factory so every
service gets identical middleware, auth, and telemetry wiring.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse a mature framework; we own only our app factory. |
| Complexity | Low: small surface, typed models, generated docs. |
| Effort | Low: same language as the AI stack means no boundary glue. |
| Scalability | High: async ASGI under Uvicorn/Gunicorn scales per service. |
| Lock-in risk | Low: standard ASGI/OpenAPI; route handlers port with modest effort. |
| Cost | Free OSS. |
| Community maturity | Very high: one of the most-used Python web frameworks. |

## Consequences

- Every service is built via `ai_os_shared/app.py`, a shared app factory that centralizes
  CORS, auth (ADR-0001), authz (ADR-0002), telemetry (ADR-0013), and error handling —
  services declare only their routes.
- Being same-language as LangGraph/LlamaIndex/LiteLLM removes any cross-runtime boundary.
- Swap path: if a service must leave FastAPI, only its routing/handler layer changes; the
  shared factory concentrates the framework-specific wiring so the blast radius is one
  module.
- Trade-off accepted: Python's raw throughput trails Go/Rust, acceptable because the
  workload is I/O-bound on model and datastore calls, not CPU-bound.
