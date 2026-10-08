"""Redis is used for rate limiting and caching. The API still works (degraded, per-process) if Redis is down."""

import logging
import time

from redis.asyncio import Redis

from app.core.config import settings

log = logging.getLogger("boq.redis")

_client: Redis | None = None
_memory_counters: dict[str, tuple[int, float]] = {}


async def init_redis() -> None:
    global _client
    if not settings.REDIS_URL:
        return
    try:
        client = Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=2)
        await client.ping()
        _client = client
    except Exception as exc:  # pragma: no cover - depends on infra
        log.warning("Redis unavailable (%s); falling back to in-process rate limiting", exc)
        _client = None


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_redis() -> Redis | None:
    return _client


async def hit(key: str, window_seconds: int) -> int:
    """Increments a fixed-window counter and returns the new count."""
    if _client is not None:
        try:
            pipe = _client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            count, _ = await pipe.execute()
            return int(count)
        except Exception as exc:  # pragma: no cover
            log.warning("Redis error during rate limit: %s", exc)
    now = time.monotonic()
    count, reset_at = _memory_counters.get(key, (0, now + window_seconds))
    if now > reset_at:
        count, reset_at = 0, now + window_seconds
    count += 1
    _memory_counters[key] = (count, reset_at)
    return count


async def reset(key: str) -> None:
    _memory_counters.pop(key, None)
    if _client is not None:
        try:
            await _client.delete(key)
        except Exception as exc:  # pragma: no cover
            log.warning("Redis error during rate-limit reset: %s", exc)
