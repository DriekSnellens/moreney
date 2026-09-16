"""Shared market-data publisher process.

Owns public WebSocket feeds once and writes latest books / ticks / health to Redis
for paper instances running in ``MARKET_DATA_MODE=shared``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any

from bot.core.config import Settings, get_settings
from bot.core.disk_guard import log_disk_guard
from bot.core.redis_client import get_redis, ping_redis
from bot.market_data.cache import MarketDataCache
from bot.market_data.research.retention import (
    effective_retention_days,
    prune_research_marketdata,
)
from bot.market_data.service import MarketDataService

logger = logging.getLogger(__name__)

_RETENTION_INTERVAL_SEC = 6 * 3600


def _run_tape_retention(settings: Settings) -> dict[str, object]:
    disk = log_disk_guard(
        "/",
        warn_pct=float(settings.disk_guard_warn_pct),
        block_pct=float(settings.disk_guard_block_pct),
    )
    days = effective_retention_days(
        configured_days=int(settings.marketdata_retention_days),
        disk_used_pct=float(disk["used_pct"]),
        warn_pct=float(settings.disk_guard_warn_pct),
        block_pct=float(settings.disk_guard_block_pct),
    )
    return prune_research_marketdata(
        settings.research_marketdata_recording_path,
        retention_days=days,
        execute_delete=True,
    )


async def run_publisher(settings: Settings | None = None) -> None:
    """Connect public feeds and publish state to Redis until cancelled."""
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not settings.research_marketdata_recording_enabled:
        logger.info(
            "RESEARCH_MARKETDATA_RECORDING_DISABLED — publisher will not write tape"
        )
    _run_tape_retention(settings)

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
    retention_mono = time.monotonic()

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
            now = time.monotonic()
            if now - retention_mono >= _RETENTION_INTERVAL_SEC:
                retention_mono = now
                _run_tape_retention(publisher_settings)
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
