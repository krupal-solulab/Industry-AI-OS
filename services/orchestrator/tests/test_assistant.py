"""Unit tests for the assistant's workflow-key awareness.

Focus: user-authored workflows (built in the visual builder, stored per-tenant in the
workflows service, `pack_key == "custom"`) must be (a) recognizable by the classifier via
`extra_workflow_keys`, and (b) started with their CORRECT pack_key. These are unit tests
around the prompt-building + pack_key mapping; the full `/chat` endpoint needs a live DB +
signed context + LLM, so it is exercised at the seam (`_gather_backend_data`) with the
workflows service call mocked. See the note in the module docstring of `main.py`.
"""

from __future__ import annotations

from orchestrator import assistant, main
from orchestrator.assistant import Intent, IntentResult, classify_intent


class _FakeLLM:
    """Captures the messages passed to `chat` and returns a canned JSON reply."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.captured: list[dict] | None = None

    async def chat(self, messages, model=None, **kwargs):
        self.captured = messages
        return self._reply


async def test_extra_workflow_keys_appear_in_classifier_prompt(monkeypatch):
    fake = _FakeLLM('{"intent": "workflow_execution", "workflow": "workflow1", "action": null}')
    monkeypatch.setattr(assistant, "get_llm", lambda: fake)

    result = await classify_intent(
        "run workflow1", [], None, "test-model", extra_workflow_keys=["workflow1", "workflow2"]
    )

    system_prompt = fake.captured[0]["content"]
    assert "workflow1" in system_prompt
    assert "workflow2" in system_prompt
    # The classification contract is unchanged: intent + workflow still resolve.
    assert result.intent is Intent.WORKFLOW_EXECUTION
    assert result.workflow == "workflow1"


async def test_extra_workflow_keys_are_deduped_order_preserved(monkeypatch):
    fake = _FakeLLM('{"intent": "general_question", "workflow": null, "action": null}')
    monkeypatch.setattr(assistant, "get_llm", lambda: fake)

    await classify_intent(
        "hello", [], None, None, extra_workflow_keys=["dup", "dup", "other"]
    )

    prompt = fake.captured[0]["content"]
    # Rendered as a Python list repr — each key single-quoted, 'dup' only once.
    assert prompt.count("'dup'") == 1
    assert "'other'" in prompt


def test_workflow_pack_map_builds_keys_and_pack_map():
    definitions = [
        {"workflow_key": "invoice_verification", "pack_key": "accounting"},
        {"workflow_key": "workflow1", "pack_key": "custom"},  # a user-built flow
        {"workflow_key": "no_pack"},  # missing pack_key → key listed, not mapped
        {"pack_key": "orphan"},  # missing workflow_key → skipped entirely
    ]

    keys, pack_by_key = main._workflow_pack_map(definitions)

    assert keys == ["invoice_verification", "workflow1", "no_pack"]
    assert pack_by_key["workflow1"] == "custom"
    assert pack_by_key["invoice_verification"] == "accounting"
    assert "no_pack" not in pack_by_key


async def test_user_workflow_started_with_custom_pack(monkeypatch):
    """A resolved user-built workflow key is started with pack_key='custom' (from the
    definitions map), not the workspace default pack."""
    captured: dict[str, str] = {}

    async def fake_start(request, pack, workflow, inputs):
        captured["pack"] = pack
        captured["workflow"] = workflow
        return {"run_id": "run-abc", "status": "awaiting_approval"}

    monkeypatch.setattr(main, "_start_pack_workflow", fake_start)

    ir = IntentResult(intent=Intent.WORKFLOW_EXECUTION, workflow="workflow1")
    block, meta = await main._gather_backend_data(
        request=None,
        ir=ir,
        ws=None,  # no industry config → no default pack; must come from the map
        use_rag=False,
        query="run workflow1",
        pack_by_key={"workflow1": "custom"},
    )

    assert captured["pack"] == "custom"
    assert captured["workflow"] == "workflow1"
    assert block is not None and "run-abc" in block
    # the started run is also surfaced structurally so the stream can emit a `workflow` frame
    assert meta.get("run") == {"run_id": "run-abc", "status": "awaiting_approval"}


async def test_seeded_workflow_falls_back_to_default_pack_when_unmapped(monkeypatch):
    """A key absent from the definitions map falls back to the workspace default pack."""
    captured: dict[str, str] = {}

    async def fake_start(request, pack, workflow, inputs):
        captured["pack"] = pack
        return {"run_id": "run-xyz", "status": "running"}

    monkeypatch.setattr(main, "_start_pack_workflow", fake_start)

    class _WS:
        workflow_packs = ["accounting"]

        class workspace:  # noqa: N801 - mirrors the Industry attribute shape
            copilots: list[str] = []

    ir = IntentResult(intent=Intent.WORKFLOW_EXECUTION, workflow="invoice_verification")
    await main._gather_backend_data(
        request=None,
        ir=ir,
        ws=_WS(),
        use_rag=False,
        query="run invoice verification",
        pack_by_key={},  # empty map → fall back to default pack
    )

    assert captured["pack"] == "accounting"
