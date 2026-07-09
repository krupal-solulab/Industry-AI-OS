"""Audit emitter — the shared way every service records a state change.

Services call `emit(...)`; the emitter ships an append-only event to the audit
service. Emission never blocks or breaks the business operation: an audit outage
is logged, not raised (the audit service itself persists durably). Every event
carries actor, tenant, timestamp, action, and before/after snapshots.
"""

from __future__ import annotations

import httpx
import structlog
from pydantic import BaseModel, Field

from ai_os_shared.settings import Settings, get_settings
from ai_os_shared.tenant_context import TenantContext, current_context

log = structlog.get_logger("aios.audit")


class AuditEvent(BaseModel):
    tenant_id: str
    actor_id: str
    actor_email: str | None = None
    action: str  # e.g. "document.upload", "workflow.approve", "connector.invoke"
    resource_kind: str
    resource_id: str
    # Timestamp is stamped by the audit service on write (authoritative clock).
    before: dict | None = None
    after: dict | None = None
    metadata: dict = Field(default_factory=dict)
    request_id: str | None = None


async def emit(
    action: str,
    *,
    resource_kind: str,
    resource_id: str,
    before: dict | None = None,
    after: dict | None = None,
    metadata: dict | None = None,
    ctx: TenantContext | None = None,
    settings: Settings | None = None,
) -> None:
    """Record a state change. Fire-and-report: failures are logged, not raised."""
    settings = settings or get_settings()
    ctx = ctx or current_context()
    if ctx is None:
        log.warning("audit.no_context", action=action)
        return
    event = AuditEvent(
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        actor_email=ctx.email,
        action=action,
        resource_kind=resource_kind,
        resource_id=resource_id,
        before=before,
        after=after,
        metadata=metadata or {},
        request_id=ctx.request_id,
    )
    url = f"{settings.audit_url.rstrip('/')}/internal/events"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=event.model_dump(mode="json"))
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        # Do not fail the business operation because auditing hiccuped.
        log.error("audit.emit_failed", action=action, error=str(exc))
