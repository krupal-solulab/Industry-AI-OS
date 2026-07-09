"""Knowledge layer — generic, industry-neutral document ingestion + RAG retrieval.

Pipeline: upload -> MinIO (object store) -> Docling (layout-aware parse) -> chunk
(LlamaIndex) -> embed (LiteLLM) -> pgvector. Retrieval runs a pgvector similarity
search scoped to the tenant. Nothing here knows about any industry.
"""
