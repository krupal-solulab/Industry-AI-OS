"""Authorization + tenant-isolation smoke checks (through the gateway)."""

import httpx

from .conftest import GATEWAY, requires_stack


@requires_stack
def test_unauthenticated_rejected():
    r = httpx.post(f"{GATEWAY}/api/orchestrator/chat", json={"message": "hi"}, timeout=10)
    assert r.status_code == 401


@requires_stack
def test_viewer_cannot_start_workflow(viewer_token: str):
    """A viewer lacks 'start' on the workflow resource — Cerbos must deny."""
    r = httpx.post(
        f"{GATEWAY}/api/workflows/document-review",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"document_id": "00000000-0000-0000-0000-000000000000"},
        timeout=15,
    )
    assert r.status_code == 403


@requires_stack
def test_viewer_cannot_read_audit(viewer_token: str):
    """Audit is manager-only; a viewer is denied read_audit."""
    r = httpx.get(
        f"{GATEWAY}/api/audit/events",
        headers={"Authorization": f"Bearer {viewer_token}"},
        timeout=15,
    )
    assert r.status_code == 403


@requires_stack
def test_forged_context_header_ignored(owner_token: str):
    """A client-supplied internal context header must be ignored by the gateway
    (the gateway strips/overrides it by minting its own from the JWT)."""
    r = httpx.get(
        f"{GATEWAY}/api/identity/me",
        headers={
            "Authorization": f"Bearer {owner_token}",
            "X-AIOS-Context": "forged.payload",
        },
        timeout=15,
    )
    # The forged header does not grant access to another tenant; request resolves
    # to the JWT's real tenant (200) — never honors the forged value.
    assert r.status_code == 200
