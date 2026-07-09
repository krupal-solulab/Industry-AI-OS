"""Langfuse tracing helper.

LiteLLM already emits per-request LLM traces to Langfuse via its callbacks; this
adds an orchestrator-level span so a chat turn is one trace with tenant/user tags.
Degrades to a no-op when Langfuse keys are unset, so the service runs without them.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import TenantContext


@contextlib.contextmanager
def trace_chat(ctx: TenantContext, model: str, prompt: str) -> Iterator[None]:
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        yield
        return

    # Setup is guarded separately from the body: a tracing failure must neither
    # break the chat turn nor cause a double-yield.
    client = trace = None
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        trace = client.trace(
            name="chat-turn",
            user_id=ctx.user_id,
            metadata={"tenant_id": ctx.tenant_id, "model": model},
            input=prompt,
            tags=[f"tenant:{ctx.tenant_id}"],
        )
    except Exception:
        yield
        return

    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            trace.update(output="(completed)")
            client.flush()
