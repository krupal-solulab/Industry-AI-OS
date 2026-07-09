"""Temporal client + worker wiring."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.worker import Worker

from ai_os_shared.settings import get_settings
from workflows.activities import record_decision, set_status, summarize_document
from workflows.workflow import DocumentReviewApproval

_client: Client | None = None


async def get_client() -> Client:
    global _client
    if _client is None:
        s = get_settings()
        _client = await Client.connect(s.temporal_host, namespace=s.temporal_namespace)
    return _client


async def build_worker() -> Worker:
    s = get_settings()
    client = await get_client()
    return Worker(
        client,
        task_queue=s.temporal_task_queue,
        workflows=[DocumentReviewApproval],
        activities=[summarize_document, set_status, record_decision],
    )
