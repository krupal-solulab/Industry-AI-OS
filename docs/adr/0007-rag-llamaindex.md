# ADR-0007: RAG framework

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — LlamaIndex

## Context

The platform's knowledge service ingests documents and retrieves relevant context for
LLM prompts. We need a framework that covers the full RAG path — chunking, embedding,
indexing, and retrieval with rerankers and query transforms — while staying pluggable on
the vector store (ADR-0008) and parser (ADR-0009) so those remain independent decisions.
It must be Python-native (ADR-0003) and run on-prem.

## Options considered

- **LangChain retrievers** — broad, but retrieval is one part of a sprawling library;
  more surface than we want and less focused on ingestion/retrieval quality.
- **Haystack** — strong, production-oriented RAG framework, but a heavier, more opinionated
  pipeline model than we need for a pluggable knowledge service.
- **Build custom** — chunkers, index abstractions, and retrievers are well-solved; rebuilding
  them is undifferentiated effort.
- **LlamaIndex** — purpose-built for RAG ingestion and retrieval, with clean abstractions
  over vector stores, embeddings, and parsers. *(chosen)*

## Decision

LlamaIndex for ingestion and retrieval pipelines, used in `services/knowledge`, with its
store/embedding/parser interfaces backed by our own ADR choices.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom retrieval framework. |
| Complexity | Moderate: rich API, but we use the ingestion/retrieval core, not everything. |
| Effort | Low: pipelines assemble from provided components. |
| Scalability | Good: retrieval scales with the backing vector store, which we control. |
| Lock-in risk | Low-moderate: store/embedding/parser are pluggable; RAG code stays in one service. |
| Cost | Free OSS. |
| Community maturity | High: a leading, actively developed RAG framework. |

## Consequences

- Ingestion and retrieval live in `services/knowledge`; LlamaIndex is wired to pgvector
  (ADR-0008) for storage and Docling (ADR-0009) for parsing through its adapter interfaces,
  keeping each swappable.
- RAG logic is confined to the knowledge service; other services request context over its
  API rather than importing LlamaIndex.
- Swap path: because the vector store and parser sit behind their own service-local
  interfaces and callers use the knowledge API, replacing LlamaIndex with Haystack or a
  custom pipeline is contained within `services/knowledge`.
- Trade-off accepted: LlamaIndex evolves quickly, so we pin versions and depend on its
  stable ingestion/retrieval core rather than experimental features.
