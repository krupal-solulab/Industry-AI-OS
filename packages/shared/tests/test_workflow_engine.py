"""Unit tests for the workflow engine core — schema validation, the expression
resolver, and end-to-end execution (including a human-approval step) with fake
handlers. No Temporal, no network: the engine logic is verified in isolation."""

import pytest
from pydantic import ValidationError

from ai_os_shared.workflow import (
    RunContext,
    StepType,
    WorkflowEngine,
    resolve,
    validate_definition,
)
from ai_os_shared.workflow.engine import EngineError
from ai_os_shared.workflow.expr import ExprError


# --------------------------------------------------------------------------- expr
def test_expr_full_reference_returns_native_type():
    ctx = {"inputs": {"n": 42, "obj": {"a": 1}}, "steps": {}}
    assert resolve("{{ inputs.n }}", ctx) == 42
    assert resolve("{{ inputs.obj }}", ctx) == {"a": 1}


def test_expr_interpolation_and_nested():
    ctx = {"inputs": {"who": "PM"}, "steps": {"ocr": {"out": {"text": "hello"}}}}
    assert resolve("Hi {{ inputs.who }}: {{ steps.ocr.out.text }}", ctx) == "Hi PM: hello"
    assert resolve({"msg": "{{ steps.ocr.out.text }}"}, ctx) == {"msg": "hello"}


def test_expr_unresolved_raises():
    with pytest.raises(ExprError):
        resolve("{{ inputs.missing }}", {"inputs": {}, "steps": {}})


# ------------------------------------------------------------------------- schema
def test_schema_rejects_duplicate_step_ids():
    bad = {
        "key": "x", "pack": "p", "trigger": {"type": "manual"},
        "steps": [
            {"id": "a", "type": "transform"},
            {"id": "a", "type": "transform"},
        ],
    }
    with pytest.raises(ValidationError):
        validate_definition(bad)


def test_schema_rejects_undeclared_connector():
    bad = {
        "key": "x", "pack": "p", "trigger": {"type": "manual"},
        "connectors_required": [],
        "steps": [{"id": "s", "type": "connector.call", "config": {"connector": "nango.outlook"}}],
    }
    with pytest.raises(ValidationError):
        validate_definition(bad)


def test_schema_rejects_approval_gate_for_missing_step():
    bad = {
        "key": "x", "pack": "p", "trigger": {"type": "manual"},
        "steps": [{"id": "s", "type": "transform"}],
        "approvals": [{"step": "nope", "approver_persona": "manager"}],
    }
    with pytest.raises(ValidationError):
        validate_definition(bad)


# ------------------------------------------------------------------------- engine
async def test_engine_runs_definition_end_to_end_with_approval():
    """A generic trigger→AI→approval→notify flow: proves data flows between steps,
    the approval gate pauses (here, resolves via a fake decision), and outputs map."""
    definition = validate_definition(
        {
            "key": "demo_review", "pack": "demo",
            "business_goal": "Summarize a document and get sign-off.",
            "trigger": {"type": "manual"},
            "inputs": [{"key": "text", "type": "string", "required": True}],
            "steps": [
                {"id": "summary", "type": "ai.action",
                 "config": {"prompt": "summarize", "input": "{{ inputs.text }}"}},
                {"id": "review", "type": "approval",
                 "config": {"summary": "{{ steps.summary.out.text }}"}},
                {"id": "send", "type": "notify",
                 "config": {"connector": "demo.echo",
                            "message": "Decision: {{ steps.review.out.decision }}"}},
            ],
            "connectors_required": ["demo.echo"],
            "approvals": [{"step": "review", "approver_persona": "reviewer"}],
            "outputs": [{"key": "decision", "from": "{{ steps.review.out.decision }}"}],
        }
    )

    calls: list[str] = []

    async def ai_action(step, cfg, ctx):
        calls.append(f"ai:{cfg['input']}")
        return {"text": f"summary of: {cfg['input']}"}

    async def approval(step, cfg, ctx):
        # Fake reviewer approves; real impl would await a Temporal signal.
        assert cfg["summary"] == "summary of: hello world"
        return {"decision": "approved", "decided_by": "reviewer@test", "comment": "ok"}

    async def notify(step, cfg, ctx):
        calls.append(f"notify:{cfg['message']}")
        return {"sent": True}

    engine = WorkflowEngine(
        {
            StepType.AI_ACTION: ai_action,
            StepType.APPROVAL: approval,
            StepType.NOTIFY: notify,
        }
    )
    result = await engine.run(definition, inputs={"text": "hello world"})

    assert calls == ["ai:hello world", "notify:Decision: approved"]
    assert result["outputs"]["decision"] == "approved"
    assert result["context"]["steps"]["summary"]["out"]["text"] == "summary of: hello world"


async def test_engine_errors_on_missing_handler():
    definition = validate_definition(
        {"key": "x", "pack": "p", "trigger": {"type": "manual"},
         "steps": [{"id": "s", "type": "ai.action", "config": {}}]}
    )
    engine = WorkflowEngine({})  # no handlers
    with pytest.raises(EngineError):
        await engine.run(definition, inputs={})


def test_run_context_addressing():
    ctx = RunContext({"a": 1})
    ctx.set_output("s1", {"x": 2})
    assert ctx.as_dict() == {"inputs": {"a": 1}, "steps": {"s1": {"out": {"x": 2}}}}
