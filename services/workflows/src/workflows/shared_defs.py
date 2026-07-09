"""Shared dataclasses for the workflow + its activities (must be import-safe from
the sandboxed workflow context, so no heavy imports here)."""

from __future__ import annotations

from dataclasses import dataclass

TASK_QUEUE_DEFAULT = "aios-workflows"
WORKFLOW_TYPE = "document_review_approval"


@dataclass
class ReviewInput:
    tenant_id: str
    document_id: str
    workflow_id: str
    submitted_by: str


@dataclass
class Decision:
    approved: bool
    decided_by: str
    comment: str = ""


@dataclass
class ReviewResult:
    status: str  # approved | rejected
    summary: str
    decided_by: str
    comment: str
