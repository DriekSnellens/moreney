"""Tests for Redis helper scaffolding."""

from bot.core import redis_client


def test_get_redis_uses_settings_url(settings, monkeypatch) -> None:
    redis_client.get_redis.cache_clear()
    monkeypatch.setenv("REDIS_URL", settings.redis_url)
    client = redis_client.get_redis(settings.redis_url)
    assert client is not None
    redis_client.get_redis.cache_clear()
