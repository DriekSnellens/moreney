"""Optional market-data recorder for research/backtesting.

Writes normalized events to JSONL files — never firehoses PostgreSQL.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bot.market_data.models import MarketDataEvent

logger = logging.getLogger(__name__)


class MarketDataRecorder:
    """Append-only JSONL recorder gated by configuration."""

    def __init__(self, *, enabled: bool, path: str) -> None:
        self.enabled = enabled
        self._path = Path(path)
        self._files: dict[str, Path] = {}
        if self.enabled:
            self._path.mkdir(parents=True, exist_ok=True)
            logger.info("MARKET_DATA_RECORDING_ENABLED path=%s", self._path)

    async def record(self, event: MarketDataEvent) -> None:
        if not self.enabled:
            return
        # Skip heartbeats to keep files manageable
        if event.event_type == "heartbeat":
            return
        day = event.received_at.strftime("%Y%m%d")
        key = f"{event.exchange}_{event.symbol}_{day}"
        file_path = self._files.get(key)
        if file_path is None:
            file_path = self._path / f"{key}.jsonl"
            self._files[key] = file_path
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
