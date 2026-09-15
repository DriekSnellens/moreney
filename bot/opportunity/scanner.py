"""Tiered scan scheduler for efficient 24/7 scanning."""

from __future__ import annotations

import time

from bot.core.config import Settings
from bot.markets.calendar import MarketCalendarService
from bot.markets.registry import InstrumentRegistry


class TieredScanScheduler:
    """Tier-1 symbols every cycle; Tier-2/3 on interval multipliers."""

    def __init__(
        self,
        settings: Settings,
        registry: InstrumentRegistry,
        calendar: MarketCalendarService,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._calendar = calendar
        self._last_scan: dict[str, float] = {}
        self._cycle = 0

    def symbols_for_cycle(self, *, all_symbols: list[str] | None = None) -> list[str]:
        self._cycle += 1
        base = all_symbols or self._registry.scan_symbols()
        if not getattr(self._settings, "global_tiered_scan_enabled", True):
            return base

        now = time.monotonic()
        selected: list[str] = []
        for sym in base:
            inst = self._registry.by_symbol(sym)
            if inst is None:
                selected.append(sym)
                continue
            if not self._calendar.is_tradeable(inst):
                continue
            mult = self._calendar.scan_interval_multiplier(inst)
            if mult <= 0:
                continue
            interval = (inst.scan_interval_ms / 1000.0) * mult
            if inst.liquidity_tier == 1:
                interval = min(interval, float(self._settings.paper_cycle_interval_ms) / 1000.0)
            last = self._last_scan.get(sym, 0.0)
            if now - last >= interval:
                self._last_scan[sym] = now
                selected.append(sym)

        return selected or base[: max(1, len(base) // 4)]

    @property
    def cycle_count(self) -> int:
        return self._cycle
