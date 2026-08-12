"""Shared market-data publisher process.

Owns public WebSocket feeds once and writes latest books / ticks / health to Redis
for paper instances running in ``MARKET_DATA_MODE=shared``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from bot.core.config import Settings, get_settings
from bot.core.redis_client import get_redis, ping_redis
from bot.market_data.cache import MarketDataCache
from bot.market_data.service import MarketDataService

logger = logging.getLogger(__name__)


async def run_publisher(settings: Settings | None = None) -> None:
    """Connect public feeds and publish state to Redis until cancelled."""
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    redis = get_redis(settings.redis_url)
    if not await ping_redis(redis):
        raise RuntimeError(f"Redis unreachable at {settings.redis_url}")

    # Publisher writes often enough for paper cycles (~1s) without drowning Redis.
    cache_interval = min(float(settings.market_data_cache_interval_ms), 100.0)
    publisher_settings = settings.model_copy(
        update={
            "market_data_mode": "publisher",
            "market_data_cache_interval_ms": cache_interval,
        }
    )
    cache = MarketDataCache(
        redis_client=redis,
        ttl_seconds=publisher_settings.market_data_redis_ttl_seconds,
    )
    service = MarketDataService(
        publisher_settings,
        cache=cache,
        start_websockets=True,
    )
    await service.start()
    logger.info(
        "MARKET_DATA_PUBLISHER_STARTED exchanges=%s symbols=%s redis=%s cache_ms=%s",
        publisher_settings.market_data_exchanges,
        publisher_settings.market_data_symbols,
        publisher_settings.redis_url,
        cache_interval,
    )

    stop = asyncio.Event()

    def _request_stop(*_args: Any) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, _request_stop)

    try:
        while not stop.is_set():
            for exchange in service.exchanges:
                health = service.get_exchange_health(exchange)
                await cache.set_health(health)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                continue
    finally:
        await service.stop()
        await redis.aclose()
        logger.info("MARKET_DATA_PUBLISHER_STOPPED")


def main() -> None:
    asyncio.run(run_publisher())


if __name__ == "__main__":
    main()
