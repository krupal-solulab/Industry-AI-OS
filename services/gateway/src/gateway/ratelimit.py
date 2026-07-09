"""Redis-backed fixed-window rate limiter, keyed per tenant+user.

Fails OPEN: if Redis is unavailable the gateway still serves traffic (availability
over strictness for a dev/on-prem baseline). Swap the algorithm here without
touching callers.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis
import structlog

from ai_os_shared.settings import get_settings

log = structlog.get_logger("aios.gateway.ratelimit")
_redis: aioredis.Redis | None = None


def _client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def allow(tenant_id: str, user_id: str) -> tuple[bool, int]:
    """Return (allowed, remaining) for the current 60s window."""
    settings = get_settings()
    limit = settings.rate_limit_per_minute
    window = int(time.time()) // 60
    key = f"rl:{tenant_id}:{user_id}:{window}"
    try:
        client = _client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 60)
        remaining = max(0, limit - count)
        return count <= limit, remaining
    except Exception as exc:
        log.warning("ratelimit.redis_unavailable", error=str(exc))
        return True, limit  # fail open
