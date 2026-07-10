"""Workflow definition schema — the POC's 11-part template as validated data.

A `WorkflowDefinition` is authored as JSON (see /packs/<industry>/workflows/*.json),
loaded, and validated here before it can ever run. Unknown step types, duplicate step
ids, approval gates that point at missing steps, or `connector.call` steps that use a
connector not declared in `connectors_required` all fail validation at load time — not
at runtime.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class StepType(str, Enum):
    CONNECTOR_CALL = "connector.call"  # invoke a connector tool (Nango/Composio/MCP)
    DOCUMENT_PARSE = "document.parse"  # OCR / layout parse a file → text
    DOCUMENT_RETRIEVE = "document.retrieve"  # semantic search (RAG)
    AI_ACTION = "ai.action"  # run a prompt over context → text/JSON
    APPROVAL = "approval"  # human-in-the-loop gate (pauses the run)
    TRANSFORM = "transform"  # map/format data via expressions (no code)
    NOTIFY = "notify"  # send a message via a connector
    BRANCH = "branch"  # conditional (phase 2)


class TriggerType(str, Enum):
    MANUAL = "manual"
    EMAIL = "email"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"


class Trigger(BaseModel):
    type: TriggerType
    source: str | None = None  # e.g. "nango.outlook"; "cron: 0 17 * * 5"
    config: dict = Field(default_factory=dict)


class IOField(BaseModel):
    key: str
    type: str = "string"
    required: bool = False
    description: str | None = None
    # For OUTPUT fields: an optional `{{ }}` expression mapping the value from the
    # run context (e.g. "{{ steps.draft.out.text }}"). Ignored for inputs.
    source: str | None = Field(None, alias="from")

    model_config = {"populate_by_name": True}


class Personas(BaseModel):
    primary: str
    supporting: list[str] = Field(default_factory=list)


class Step(BaseModel):
    id: str
    type: StepType
    name: str | None = None
    config: dict = Field(default_factory=dict)
    # Optional friendly key for this step's output in the context (defaults to id).
    out: str | None = None


class ApprovalGate(BaseModel):
    step: str  # id of the `approval` step this gate configures
    approver_persona: str  # persona (resolved to platform roles by the pack)


class WorkflowDefinition(BaseModel):
    schema_version: str = Field("aios.workflow/v1", alias="$schema")
    key: str
    pack: str
    version: str = "1.0.0"
    business_goal: str = ""
    personas: Personas | None = None
    trigger: Trigger
    inputs: list[IOField] = Field(default_factory=list)
    connectors_required: list[str] = Field(default_factory=list)
    steps: list[Step]
    approvals: list[ApprovalGate] = Field(default_factory=list)
    outputs: list[IOField] = Field(default_factory=list)
    business_value: str = ""

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate(self) -> WorkflowDefinition:
        ids = [s.id for s in self.steps]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Duplicate step ids: {sorted(dupes)}")
        idset = set(ids)
        for gate in self.approvals:
            if gate.step not in idset:
                raise ValueError(f"Approval gate references unknown step '{gate.step}'")
        declared = set(self.connectors_required)
        for step in self.steps:
            if step.type in (StepType.CONNECTOR_CALL, StepType.NOTIFY):
                connector = step.config.get("connector")
                if connector and connector not in declared:
                    raise ValueError(
                        f"Step '{step.id}' uses connector '{connector}' not listed in "
                        f"connectors_required"
                    )
        return self


class PersonaRole(BaseModel):
    persona: str
    roles: list[str]  # platform roles (owner/admin/member/viewer) that hold this persona


class PackManifest(BaseModel):
    key: str
    name: str
    industry: str
    version: str = "1.0.0"
    description: str = ""
    personas: list[str] = Field(default_factory=list)
    persona_roles: list[PersonaRole] = Field(default_factory=list)
    connectors: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)  # workflow keys in the pack


def validate_definition(data: dict) -> WorkflowDefinition:
    """Parse + validate a raw definition dict. Raises pydantic ValidationError/ValueError."""
    return WorkflowDefinition.model_validate(data)
