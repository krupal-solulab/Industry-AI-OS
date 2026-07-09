"""Audit service.

Two surfaces:
  - `POST /internal/events` — server-to-server ingest used by the shared audit
    emitter. Not exposed through the gateway; tenant comes from the event body.
  - `GET /events` + `GET /events/{id}` — tenant-scoped query API (behind the
    gateway), guarded by Cerbos `read_audit` on the `audit` resource and filtered
    by RLS so a tenant only ever sees its own trail.

Writes are append-only: the `audit_log` table rejects UPDATE/DELETE at the DB level.
"""

from __future__ import annotations

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import text

from ai_os_shared.app import create_app
from ai_os_shared.audit import AuditEvent
from ai_os_shared.authz import check_ctx
from ai_os_shared.db import admin_session, get_engine, tenant_session
from ai_os_shared.health import HealthRegistry
from ai_os_shared.tenant_context import TenantContext, require_context
from ai_os_shared.types import Resource

health = HealthRegistry("audit")


async def _db_check() -> str:
    async with admin_session() as s:
        await s.execute(text("SELECT 1"))
    return "ok"


health.register("postgres", _db_check)

app = create_app(service_name="audit", title="AIOS Audit Service", health_registry=health)


@app.on_event("startup")
async def _startup() -> None:
    get_engine()


class AuditEventOut(BaseModel):
    id: str
    tenant_id: str
    actor_id: str
    actor_email: str | None
    action: str
    resource_kind: str
    resource_id: str
    before: dict | None
    after: dict | None
    metadata: dict
    request_id: str | None
    created_at: str


@app.post("/internal/events", status_code=201, tags=["internal"])
async def ingest(event: AuditEvent) -> dict:
    """Append one audit event. Tenant scoping comes from the event body (the caller
    is a trusted internal service). RLS is satisfied by binding that tenant."""
    ctx = TenantContext(tenant_id=event.tenant_id, user_id=event.actor_id)
    async with tenant_session(ctx) as s:
        row = await s.execute(
            text(
                """
                INSERT INTO audit_log
                    (tenant_id, actor_id, actor_email, action, resource_kind,
                     resource_id, before, after, metadata, request_id)
                VALUES
                    (:tenant_id, :actor_id, :actor_email, :action, :resource_kind,
                     :resource_id, CAST(:before AS jsonb), CAST(:after AS jsonb),
                     CAST(:metadata AS jsonb), :request_id)
                RETURNING id, created_at
                """
            ),
            {
                "tenant_id": event.tenant_id,
                "actor_id": event.actor_id,
                "actor_email": event.actor_email,
                "action": event.action,
                "resource_kind": event.resource_kind,
                "resource_id": event.resource_id,
                "before": _json(event.before),
                "after": _json(event.after),
                "metadata": _json(event.metadata),
                "request_id": event.request_id,
            },
        )
        rec = row.one()
    return {"id": str(rec.id), "created_at": rec.created_at.isoformat()}


@app.get("/events", tags=["audit"])
async def list_events(
    action: str | None = Query(None),
    resource_kind: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
) -> list[AuditEventOut]:
    ctx = require_context()
    await check_ctx(
        ctx, "read_audit", Resource(kind="audit", id="*", tenant_id=ctx.tenant_id)
    )
    clauses = ["tenant_id = :tid"]
    params: dict = {"tid": ctx.tenant_id, "limit": limit, "offset": offset}
    if action:
        clauses.append("action = :action")
        params["action"] = action
    if resource_kind:
        clauses.append("resource_kind = :rk")
        params["rk"] = resource_kind
    where = " AND ".join(clauses)
    async with tenant_session(ctx) as s:
        rows = await s.execute(
            text(
                f"""
                SELECT id, tenant_id, actor_id, actor_email, action, resource_kind,
                       resource_id, before, after, metadata, request_id, created_at
                FROM audit_log
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [_to_out(r) for r in rows.mappings().all()]


@app.get("/events/{event_id}", tags=["audit"])
async def get_event(event_id: str) -> AuditEventOut:
    from ai_os_shared.errors import NotFoundError

    ctx = require_context()
    await check_ctx(
        ctx, "read_audit", Resource(kind="audit", id=event_id, tenant_id=ctx.tenant_id)
    )
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT id, tenant_id, actor_id, actor_email, action, resource_kind,
                           resource_id, before, after, metadata, request_id, created_at
                    FROM audit_log WHERE id = :id
                    """
                ),
                {"id": event_id},
            )
        ).mappings().first()
    if not row:
        raise NotFoundError("Audit event not found")
    return _to_out(row)


def _json(value) -> str | None:
    import json

    return None if value is None else json.dumps(value)


def _to_out(r) -> AuditEventOut:
    return AuditEventOut(
        id=str(r["id"]),
        tenant_id=r["tenant_id"],
        actor_id=r["actor_id"],
        actor_email=r["actor_email"],
        action=r["action"],
        resource_kind=r["resource_kind"],
        resource_id=r["resource_id"],
        before=r["before"],
        after=r["after"],
        metadata=r["metadata"],
        request_id=r["request_id"],
        created_at=r["created_at"].isoformat(),
    )
