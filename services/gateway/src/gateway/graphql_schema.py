"""GraphQL surface (Strawberry), mirroring the REST capabilities.

Resolvers proxy to downstream services with a freshly minted signed context, so
tenancy + authz are enforced identically to the REST path. The frontend track can
use either REST or GraphQL.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from ai_os_shared.auth import INTERNAL_HEADER, mint_context_header
from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import TenantContext


async def _call(service_attr: str, method: str, path: str, ctx: TenantContext, json=None):
    import httpx

    base = getattr(get_settings(), service_attr)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(
            method,
            f"{base.rstrip('/')}{path}",
            headers={INTERNAL_HEADER: mint_context_header(ctx)},
            json=json,
        )
        resp.raise_for_status()
        return resp.json()


def _ctx(info: Info) -> TenantContext:
    ctx = info.context.get("tenant_context")
    if ctx is None:
        raise Exception("Not authenticated")
    return ctx


@strawberry.type
class Me:
    user_id: str
    email: str | None
    tenant_id: str
    roles: list[str]


@strawberry.type
class AuditEvent:
    id: str
    action: str
    resource_kind: str
    resource_id: str
    actor_id: str
    created_at: str


@strawberry.type
class Workflow:
    workflow_id: str
    type: str
    status: str
    decision: str | None


@strawberry.type
class ChatReply:
    session_id: str
    model: str
    answer: str


@strawberry.type
class WorkflowRef:
    workflow_id: str
    status: str


@strawberry.type
class Query:
    @strawberry.field
    async def me(self, info: Info) -> Me:
        ctx = _ctx(info)
        data = await _call("identity_url", "GET", "/me", ctx)
        return Me(
            user_id=data["user_id"], email=data.get("email"),
            tenant_id=data["tenant_id"], roles=data["roles"],
        )

    @strawberry.field
    async def audit_events(self, info: Info, limit: int = 50) -> list[AuditEvent]:
        ctx = _ctx(info)
        rows = await _call("audit_url", "GET", f"/events?limit={limit}", ctx)
        return [
            AuditEvent(
                id=r["id"], action=r["action"], resource_kind=r["resource_kind"],
                resource_id=r["resource_id"], actor_id=r["actor_id"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    @strawberry.field
    async def workflows(self, info: Info) -> list[Workflow]:
        ctx = _ctx(info)
        rows = await _call("workflows_url", "GET", "/workflows", ctx)
        return [
            Workflow(
                workflow_id=r["workflow_id"], type=r["type"], status=r["status"],
                decision=r.get("decision"),
            )
            for r in rows
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def chat(
        self, info: Info, message: str, session_id: str | None = None, use_rag: bool = False
    ) -> ChatReply:
        ctx = _ctx(info)
        data = await _call(
            "orchestrator_url", "POST", "/chat", ctx,
            json={"message": message, "session_id": session_id, "use_rag": use_rag},
        )
        return ChatReply(
            session_id=data["session_id"], model=data["model"], answer=data["answer"]
        )

    @strawberry.mutation
    async def start_document_review(self, info: Info, document_id: str) -> WorkflowRef:
        ctx = _ctx(info)
        data = await _call(
            "workflows_url", "POST", "/workflows/document-review", ctx,
            json={"document_id": document_id},
        )
        return WorkflowRef(workflow_id=data["workflow_id"], status=data["status"])

    @strawberry.mutation
    async def approve_workflow(
        self, info: Info, workflow_id: str, comment: str = ""
    ) -> WorkflowRef:
        ctx = _ctx(info)
        await _call(
            "workflows_url", "POST", f"/workflows/{workflow_id}/approve", ctx,
            json={"comment": comment},
        )
        return WorkflowRef(workflow_id=workflow_id, status="approved")

    @strawberry.mutation
    async def reject_workflow(
        self, info: Info, workflow_id: str, comment: str = ""
    ) -> WorkflowRef:
        ctx = _ctx(info)
        await _call(
            "workflows_url", "POST", f"/workflows/{workflow_id}/reject", ctx,
            json={"comment": comment},
        )
        return WorkflowRef(workflow_id=workflow_id, status="rejected")


schema = strawberry.Schema(query=Query, mutation=Mutation)
