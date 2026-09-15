"""Market session calendar for 24/7 multi-market operation."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.core.enums import AssetClass, MarketSessionPhase
from bot.markets.instrument import NormalizedInstrument


def _weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5


class MarketCalendarService:
    """Session awareness: crypto always open; FX/equity follow simplified calendars."""

    FX_OPEN = time(0, 0)
    FX_CLOSE = time(23, 59)
    US_PRE = time(4, 0)
    US_OPEN = time(9, 30)
    US_CLOSE = time(16, 0)
    US_AFTER = time(20, 0)
    EU_OPEN = time(8, 0)
    EU_CLOSE = time(17, 30)

    def phase(self, instrument: NormalizedInstrument, *, now: datetime | None = None) -> MarketSessionPhase:
        ts = now or datetime.now(UTC)
        if not instrument.session_aware or instrument.asset_class in {
            AssetClass.CRYPTO_SPOT,
            AssetClass.CRYPTO_PERP,
        }:
            return MarketSessionPhase.ALWAYS_OPEN

        if instrument.asset_class == AssetClass.FX:
            return self._fx_phase(ts)

        if instrument.asset_class == AssetClass.EQUITY:
            return self._equity_phase(instrument, ts)

        return MarketSessionPhase.CLOSED

    def is_tradeable(self, instrument: NormalizedInstrument, *, now: datetime | None = None) -> bool:
        phase = self.phase(instrument, now=now)
        return phase in {
            MarketSessionPhase.ALWAYS_OPEN,
            MarketSessionPhase.REGULAR,
            MarketSessionPhase.PRE_MARKET,
            MarketSessionPhase.AFTER_HOURS,
        }

    def scan_interval_multiplier(self, instrument: NormalizedInstrument, *, now: datetime | None = None) -> float:
        phase = self.phase(instrument, now=now)
        if phase == MarketSessionPhase.ALWAYS_OPEN:
            return 1.0 if instrument.liquidity_tier == 1 else 1.5
        if phase == MarketSessionPhase.REGULAR:
            return 1.0
        if phase in {MarketSessionPhase.PRE_MARKET, MarketSessionPhase.AFTER_HOURS}:
            return 2.0
        return 0.0

    def active_asset_classes(self, *, now: datetime | None = None) -> set[AssetClass]:
        ts = now or datetime.now(UTC)
        active = {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}
        if self._fx_phase(ts) != MarketSessionPhase.CLOSED:
            active.add(AssetClass.FX)
        if (
            self._equity_phase_us(ts) != MarketSessionPhase.CLOSED
            or self._equity_phase_eu(ts) != MarketSessionPhase.CLOSED
        ):
            active.add(AssetClass.EQUITY)
        return active

    def _fx_phase(self, ts: datetime) -> MarketSessionPhase:
        if _weekend(ts):
            return MarketSessionPhase.CLOSED
        return MarketSessionPhase.REGULAR

    def _equity_phase(self, instrument: NormalizedInstrument, ts: datetime) -> MarketSessionPhase:
        if instrument.symbol.endswith(".US"):
            return self._equity_phase_us(ts)
        return self._equity_phase_eu(ts)

    def _equity_phase_us(self, ts: datetime) -> MarketSessionPhase:
        local = ts.astimezone(ZoneInfo("America/New_York"))
        if _weekend(local):
            return MarketSessionPhase.CLOSED
        t = local.time()
        if self.US_PRE <= t < self.US_OPEN:
            return MarketSessionPhase.PRE_MARKET
        if self.US_OPEN <= t < self.US_CLOSE:
            return MarketSessionPhase.REGULAR
        if self.US_CLOSE <= t < self.US_AFTER:
            return MarketSessionPhase.AFTER_HOURS
        return MarketSessionPhase.CLOSED

    def _equity_phase_eu(self, ts: datetime) -> MarketSessionPhase:
        local = ts.astimezone(ZoneInfo("Europe/Amsterdam"))
        if _weekend(local):
            return MarketSessionPhase.CLOSED
        t = local.time()
        if self.EU_OPEN <= t < self.EU_CLOSE:
            return MarketSessionPhase.REGULAR
        return MarketSessionPhase.CLOSED

    def next_open(self, instrument: NormalizedInstrument, *, now: datetime | None = None) -> datetime | None:
        if self.is_tradeable(instrument, now=now):
            return None
        ts = now or datetime.now(UTC)
        if instrument.asset_class in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}:
            return ts
        probe = ts
        for _ in range(7 * 24):
            probe += timedelta(hours=1)
            if self.is_tradeable(instrument, now=probe):
                return probe
        return None
