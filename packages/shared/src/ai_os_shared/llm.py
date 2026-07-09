"""LLM access — provider-agnostic via the LiteLLM gateway.

Nothing in the platform talks to Anthropic/OpenAI directly. Services call LiteLLM
through this client, referencing model *aliases* (e.g. "claude-primary") resolved by
the LiteLLM proxy config (`deploy/litellm/config.yaml`). Swapping providers or adding
fallbacks is a proxy-config change, not a code change.

Per-tenant model configuration is supported by passing `model=` explicitly; the
orchestrator resolves a tenant's configured model before calling here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from ai_os_shared.errors import UpstreamError
from ai_os_shared.settings import Settings, get_settings


class LLMClient:
    """Minimal OpenAI-compatible client pointed at the LiteLLM proxy."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base = self._settings.litellm_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {self._settings.litellm_master_key}"}

    async def chat(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> str:
        model = model or self._settings.default_chat_model
        payload = {"model": model, "messages": messages, "stream": False, **kwargs}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base}/v1/chat/completions", json=payload, headers=self._headers
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"LiteLLM chat failed: {exc}") from exc
        return data["choices"][0]["message"]["content"]

    async def stream_chat(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> AsyncIterator[str]:
        """Yield content deltas as they arrive (SSE), for streaming chat endpoints."""
        model = model or self._settings.default_chat_model
        payload = {"model": model, "messages": messages, "stream": True, **kwargs}
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self._base}/v1/chat/completions",
                    json=payload,
                    headers=self._headers,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        chunk = line[len("data: ") :]
                        if chunk.strip() == "[DONE]":
                            break
                        import json

                        try:
                            delta = json.loads(chunk)["choices"][0]["delta"].get("content")
                        except (KeyError, IndexError, ValueError):
                            continue
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise UpstreamError(f"LiteLLM stream failed: {exc}") from exc

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or self._settings.default_embed_model
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base}/v1/embeddings",
                    json={"model": model, "input": texts},
                    headers=self._headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"LiteLLM embed failed: {exc}") from exc
        return [item["embedding"] for item in data["data"]]


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
