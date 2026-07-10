"""The generic workflow engine.

`WorkflowEngine` interprets a `WorkflowDefinition`: for each step it resolves the
step's config against the run context (via `expr.resolve`), invokes the registered
handler for that step type, and stores the handler's output back into the context so
later steps can reference it. This class is pure and side-effect-free on its own — all
IO (connectors, AI, approvals, persistence) lives in the injected handlers, which
makes the engine fully unit-testable with fakes.

Human approval is *just another step handler*: in tests it returns a decision
immediately; under Temporal it blocks awaiting a signal. Executors that prefer
suspend/resume semantics can have a handler raise `PendingApproval`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ai_os_shared.workflow.expr import resolve
from ai_os_shared.workflow.schema import Step, StepType, WorkflowDefinition


class EngineError(Exception):
    pass


class PendingApproval(Exception):
    """A handler may raise this to suspend a run until a human decision arrives.

    Carries the step id so a durable executor can persist state and resume later.
    The default (blocking) approval handler does not need it.
    """

    def __init__(self, step_id: str) -> None:
        super().__init__(f"Pending approval at step '{step_id}'")
        self.step_id = step_id


class RunContext:
    """Per-run data: the trigger inputs plus each step's output, addressable by
    `inputs.*` and `steps.<id>.out.*` in `{{ }}` expressions."""

    def __init__(self, inputs: dict | None = None) -> None:
        self.inputs: dict = inputs or {}
        self.steps: dict[str, dict] = {}

    def as_dict(self) -> dict:
        return {"inputs": self.inputs, "steps": self.steps}

    def set_output(self, key: str, output: dict) -> None:
        self.steps[key] = {"out": output}


# handler(step, resolved_config, ctx) -> output dict
StepHandler = Callable[[Step, dict, RunContext], Awaitable[dict]]
EventHook = Callable[[str, Step, dict | None], None]


class WorkflowEngine:
    def __init__(self, handlers: dict[StepType, StepHandler], on_event: EventHook | None = None):
        self.handlers = handlers
        self.on_event = on_event

    def _emit(self, kind: str, step: Step, data: dict | None) -> None:
        if self.on_event:
            self.on_event(kind, step, data)

    async def run(self, definition: WorkflowDefinition, inputs: dict | None = None) -> dict:
        ctx = RunContext(inputs)
        for step in definition.steps:
            handler = self.handlers.get(step.type)
            if handler is None:
                raise EngineError(f"No handler registered for step type '{step.type.value}'")
            resolved = resolve(step.config, ctx.as_dict())
            self._emit("step_start", step, resolved)
            output = await handler(step, resolved, ctx)
            if not isinstance(output, dict):
                output = {"value": output}
            ctx.set_output(step.out or step.id, output)
            self._emit("step_complete", step, output)

        return {
            "context": ctx.as_dict(),
            "outputs": self._collect_outputs(definition, ctx),
        }

    @staticmethod
    def _collect_outputs(definition: WorkflowDefinition, ctx: RunContext) -> dict:
        """Map declared outputs from the context via their optional `from` expression."""
        result: dict = {}
        for field in definition.outputs:
            result[field.key] = resolve(field.source, ctx.as_dict()) if field.source else None
        return result
