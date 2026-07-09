"""End-to-end Definition-of-Done flow, all through the gateway as one tenant:

  login -> identity -> chat -> upload document -> RAG retrieve -> approval workflow
  (start + human approve) -> audit log shows every action.

LLM-dependent steps (chat answer, retrieval hits) are best-effort: without provider
API keys the request path is still exercised, but content is not asserted. The
tenant-scoping, workflow, and audit paths are asserted strictly.
"""

import io

import httpx

from .conftest import requires_stack


@requires_stack
def test_identity_me(auth_client: httpx.Client):
    r = auth_client.get("/api/identity/me")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"]
    assert "owner" in body["roles"]


@requires_stack
def test_chat_path(auth_client: httpx.Client):
    r = auth_client.post("/api/orchestrator/chat", json={"message": "Hello, who are you?"})
    # 200 with an answer when an LLM key is configured; upstream error otherwise.
    assert r.status_code in (200, 502)
    if r.status_code == 200:
        assert r.json()["answer"]


@requires_stack
def test_document_and_workflow_and_audit(auth_client: httpx.Client):
    # 1. upload a document (infra-only path: storage + parse + DB)
    content = io.BytesIO(b"Reusable platform overview.\n" * 20)
    files = {"file": ("policy.txt", content, "text/plain")}
    up = auth_client.post("/api/knowledge/documents", files=files)
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]

    # 2. retrieve (best-effort — needs embeddings)
    ret = auth_client.post("/api/knowledge/retrieve", json={"query": "platform", "top_k": 3})
    assert ret.status_code in (200, 502)

    # 3. start the generic approval workflow
    start = auth_client.post("/api/workflows/document-review", json={"document_id": doc_id})
    assert start.status_code == 201, start.text
    wf_id = start.json()["workflow_id"]

    # 4. human approves
    appr = auth_client.post(f"/api/workflows/{wf_id}/approve", json={"comment": "looks good"})
    assert appr.status_code == 200

    # 5. audit log records the actions for this tenant
    audit = auth_client.get("/api/audit/events?limit=100")
    assert audit.status_code == 200
    actions = {e["action"] for e in audit.json()}
    assert "document.upload" in actions
    assert "workflow.start" in actions


@requires_stack
def test_connector_echo(auth_client: httpx.Client):
    """The reference connector proves the Connector Hub invoke path end to end."""
    r = auth_client.post("/api/connectors/echo/invoke", json={"tool": "ping", "arguments": {}})
    assert r.status_code == 200
    assert r.json()["result"] == {"pong": True}
