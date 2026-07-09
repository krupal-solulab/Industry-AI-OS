"""Knowledge service endpoints: upload/ingest, retrieve (RAG), list, get."""

from __future__ import annotations

import structlog
from fastapi import File, UploadFile
from pydantic import BaseModel
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import emit
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import admin_session, get_engine, new_uuid, tenant_session
from ai_os_shared.errors import NotFoundError
from ai_os_shared.health import HealthRegistry
from ai_os_shared.llm import get_llm
from ai_os_shared.tenant_context import require_context
from ai_os_shared.types import Resource
from knowledge.parsing import chunk_text, parse_to_text
from knowledge.storage import get_client, put_object

log = structlog.get_logger("aios.knowledge")
health = HealthRegistry("knowledge")


async def _db_check() -> str:
    async with admin_session() as s:
        await s.execute(text("SELECT 1"))
    return "ok"


async def _minio_check() -> str:
    get_client()  # constructs client + ensures bucket
    return "ok"


health.register("postgres", _db_check)
health.register("minio", _minio_check)

app = create_app(service_name="knowledge", title="AIOS Knowledge Service", health_registry=health)


@app.on_event("startup")
async def _startup() -> None:
    get_engine()


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


class DocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    chunks: int = 0


@app.post("/documents", response_model=DocumentOut, status_code=201, tags=["knowledge"])
async def upload(file: UploadFile = File(...)) -> DocumentOut:
    ctx = require_context()
    await check_ctx(ctx, "upload", Resource(kind="document", id="*", tenant_id=ctx.tenant_id))

    data = await file.read()
    doc_id = new_uuid()
    object_key = f"{ctx.tenant_id}/{doc_id}/{file.filename}"

    # 1. store the raw object
    put_object(object_key, data, file.content_type)

    # 2. record the document
    async with tenant_session(ctx) as s:
        await s.execute(
            text(
                """INSERT INTO documents
                   (id, tenant_id, filename, content_type, object_key, status,
                    size_bytes, created_by)
                   VALUES (:id, :tid, :fn, :ct, :ok, 'processing', :sz, :by)"""
            ),
            {
                "id": doc_id, "tid": ctx.tenant_id, "fn": file.filename,
                "ct": file.content_type, "ok": object_key, "sz": len(data),
                "by": ctx.user_id,
            },
        )

    # 3. parse + chunk
    chunks = chunk_text(parse_to_text(file.filename, data))

    # 4. embed + store (best-effort: a missing embedding provider must not lose the doc)
    status = "processed"
    stored = 0
    if chunks:
        try:
            embeddings = await get_llm().embed(chunks)
            async with tenant_session(ctx) as s:
                for idx, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=False)):
                    await s.execute(
                        text(
                            """INSERT INTO document_chunks
                               (tenant_id, document_id, chunk_index, content, embedding)
                               VALUES (:tid, :doc, :idx, :content, CAST(:emb AS vector))"""
                        ),
                        {
                            "tid": ctx.tenant_id, "doc": doc_id, "idx": idx,
                            "content": chunk, "emb": _vec_literal(emb),
                        },
                    )
            stored = len(chunks)
        except Exception as exc:  # embeddings unavailable (e.g. no API key)
            log.warning("embed.failed", error=str(exc))
            status = "stored_without_embeddings"

    async with tenant_session(ctx) as s:
        await s.execute(
            text("UPDATE documents SET status = :st WHERE id = :id"),
            {"st": status, "id": doc_id},
        )

    await emit(
        "document.upload",
        resource_kind="document",
        resource_id=doc_id,
        after={"filename": file.filename, "status": status, "chunks": stored},
    )
    return DocumentOut(id=doc_id, filename=file.filename, status=status, chunks=stored)


@app.get("/documents", tags=["knowledge"])
async def list_documents(limit: int = 100) -> list[dict]:
    ctx = require_context()
    await check_ctx(ctx, "list", Resource(kind="document", id="*", tenant_id=ctx.tenant_id))
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text(
                "SELECT id, filename, content_type, status, size_bytes, created_at "
                "FROM documents ORDER BY created_at DESC LIMIT :lim"
            ),
            {"lim": limit},
        )
        return [
            {
                "id": str(r.id), "filename": r.filename, "content_type": r.content_type,
                "status": r.status, "size_bytes": r.size_bytes,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = 4


class RetrievedChunk(BaseModel):
    document_id: str
    chunk_index: int
    content: str
    score: float


@app.post("/retrieve", tags=["knowledge"])
async def retrieve(req: RetrieveRequest) -> dict:
    """Semantic search over the tenant's chunks (pgvector cosine distance)."""
    ctx = require_context()
    await check_ctx(ctx, "retrieve", Resource(kind="document", id="*", tenant_id=ctx.tenant_id))
    query_emb = (await get_llm().embed([req.query]))[0]
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text(
                """SELECT document_id, chunk_index, content,
                          1 - (embedding <=> CAST(:q AS vector)) AS score
                   FROM document_chunks
                   WHERE embedding IS NOT NULL
                   ORDER BY embedding <=> CAST(:q AS vector)
                   LIMIT :k"""
            ),
            {"q": _vec_literal(query_emb), "k": req.top_k},
        )
        results = [
            RetrievedChunk(
                document_id=str(r.document_id),
                chunk_index=r.chunk_index,
                content=r.content,
                score=float(r.score),
            ).model_dump()
            for r in rows
        ]
    return {"query": req.query, "results": results}


@app.get("/documents/{doc_id}", tags=["knowledge"])
async def get_document(doc_id: str) -> dict:
    ctx = require_context()
    await check_ctx(
        ctx, "read", Resource(kind="document", id=doc_id, tenant_id=ctx.tenant_id)
    )
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, filename, content_type, status, size_bytes, created_at "
                    "FROM documents WHERE id = :id"
                ),
                {"id": doc_id},
            )
        ).first()
    if not row:
        raise NotFoundError("Document not found")
    return {
        "id": str(row.id), "filename": row.filename, "content_type": row.content_type,
        "status": row.status, "size_bytes": row.size_bytes,
        "created_at": row.created_at.isoformat(),
    }
