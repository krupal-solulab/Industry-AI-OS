"""Workflow Pack Framework — the declarative workflow engine (Milestone 2).

A workflow is DATA (a validated definition), not code. `WorkflowEngine` interprets a
`WorkflowDefinition` by running its steps in order against a set of pluggable step
handlers, threading data between steps through a `RunContext` resolved by a safe
expression resolver (no `eval`). The same engine powers the demo pack and every
industry pack; only the definitions, prompts, and connectors change.
"""

from ai_os_shared.workflow.engine import (
    PendingApproval,
    RunContext,
    StepHandler,
    WorkflowEngine,
)
from ai_os_shared.workflow.expr import resolve
from ai_os_shared.workflow.registry import PackError, load_all_packs, load_pack
from ai_os_shared.workflow.schema import (
    ApprovalGate,
    IOField,
    PackManifest,
    Personas,
    Step,
    StepType,
    Trigger,
    TriggerType,
    WorkflowDefinition,
    validate_definition,
)

__all__ = [
    "WorkflowDefinition",
    "PackManifest",
    "Step",
    "StepType",
    "Trigger",
    "TriggerType",
    "Personas",
    "ApprovalGate",
    "IOField",
    "validate_definition",
    "resolve",
    "WorkflowEngine",
    "RunContext",
    "StepHandler",
    "PendingApproval",
    "load_pack",
    "load_all_packs",
    "PackError",
]
