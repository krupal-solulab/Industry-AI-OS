"""Health checks — a uniform readiness/liveness contract for every service.

`/healthz` is liveness (process is up). `/readyz` runs registered dependency checks
(DB, Cerbos, LiteLLM, …) so orchestrators and `make health` get a real signal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ai_os_shared.types import HealthReport, HealthStatus

CheckFn = Callable[[], Awaitable[str]]


class HealthRegistry:
    """Collects named async dependency checks for a service."""

    def __init__(self, service: str) -> None:
        self.service = service
        self._checks: dict[str, CheckFn] = {}

    def register(self, name: str, fn: CheckFn) -> None:
        self._checks[name] = fn

    async def report(self) -> HealthReport:
        checks: dict[str, str] = {}
        status = HealthStatus.OK
        for name, fn in self._checks.items():
            try:
                checks[name] = await fn()
            except Exception as exc:  # a failing dependency degrades, not crashes
                checks[name] = f"error: {exc}"
                status = HealthStatus.DEGRADED
        return HealthReport(service=self.service, status=status, checks=checks)
