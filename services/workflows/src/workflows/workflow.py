"""The generic document-review-with-approval workflow.

submit -> AI summarize -> WAIT for human approve/reject (signal) -> record + audit.

The human step is a durable wait: the workflow can sleep for days waiting for a
signal without consuming resources — this is why Temporal is used rather than an
in-process background task.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities import record_decision, set_status, summarize_document
    from workflows.shared_defs import Decision, ReviewInput, ReviewResult

_ACT = dict(
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy=RetryPolicy(maximum_attempts=3),
)


@workflow.defn(name="document_review_approval")
class DocumentReviewApproval:
    def __init__(self) -> None:
        self._decision: Decision | None = None
        self._summary: str = ""
        self._status: str = "running"

    @workflow.run
    async def run(self, inp: ReviewInput) -> ReviewResult:
        self._status = "summarizing"
        self._summary = await workflow.execute_activity(
            summarize_document, inp, **_ACT
        )
        await workflow.execute_activity(
            set_status, args=[inp.tenant_id, inp.workflow_id, "awaiting_approval", self._summary],
            **_ACT,
        )
        self._status = "awaiting_approval"

        # Durable human-in-the-loop wait: block until a decision signal arrives.
        await workflow.wait_condition(lambda: self._decision is not None)
        assert self._decision is not None

        result = ReviewResult(
            status="approved" if self._decision.approved else "rejected",
            summary=self._summary,
            decided_by=self._decision.decided_by,
            comment=self._decision.comment,
        )
        self._status = result.status
        await workflow.execute_activity(record_decision, args=[inp, result], **_ACT)
        return result

    @workflow.signal
    async def decide(self, decision: Decision) -> None:
        """Human approve/reject arrives here (idempotent: first decision wins)."""
        if self._decision is None:
            self._decision = decision

    @workflow.query
    def status(self) -> str:
        return self._status

    @workflow.query
    def summary(self) -> str:
        return self._summary
