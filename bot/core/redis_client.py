"""Redis connection helper (cache / future pub-sub). Credentials via REDIS_URL only."""

from functools import lru_cache
from typing import Any

from redis.asyncio import Redis

from bot.core.config import Settings, get_settings


@lru_cache
def get_redis(url: str | None = None) -> Redis:
    """Return a shared async Redis client."""
    settings: Settings = get_settings()
    return Redis.from_url(url or settings.redis_url, decode_responses=True)


async def ping_redis(client: Redis | None = None) -> bool:
    """Health-check Redis connectivity."""
    redis = client or get_redis()
    response: Any = await redis.ping()
    return bool(response)
