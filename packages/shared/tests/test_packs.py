"""Validate that every shipped pack (demo + construction) loads and passes schema +
manifest validation. This is the guardrail that keeps workflow definitions honest."""

from pathlib import Path

from ai_os_shared.workflow import load_all_packs, load_pack

# ai-backend/packages/shared/tests/ -> parents[3] == ai-backend
PACKS_ROOT = Path(__file__).resolve().parents[3] / "packs"


def test_all_packs_load_and_validate():
    packs = load_all_packs(PACKS_ROOT)
    assert "demo" in packs
    assert "construction" in packs


def test_demo_pack_has_document_review():
    _manifest, defs = load_pack(PACKS_ROOT / "demo")
    assert "document_review" in defs
    wf = defs["document_review"]
    assert any(s.type.value == "approval" for s in wf.steps)


def test_construction_pack_has_five_workflows():
    manifest, defs = load_pack(PACKS_ROOT / "construction")
    assert manifest.industry == "construction"
    assert set(defs) == {
        "rfi",
        "change_order",
        "daily_report",
        "invoice_verification",
        "progress_report",
    }
    # Every construction workflow must have a human-approval gate.
    for wf in defs.values():
        assert wf.approvals, f"{wf.key} has no approval gate"
        assert any(s.type.value == "approval" for s in wf.steps)
