"""Perp funding rate polling for Phase-2 funding/basis strategy."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from bot.core.config import Settings

logger = logging.getLogger(__name__)

_BINANCE_PREMIUM_INDEX = "https://fapi.binance.com/fapi/v1/premiumIndex"


def perp_symbol_for(spot_symbol: str) -> str | None:
    """Map spot symbol to a USDT-margined perp ticker on Binance."""
    sym = spot_symbol.upper().replace("-", "").replace("/", "")
    if sym.endswith("USDT"):
        return sym
    if sym.endswith("EUR") and len(sym) > 3:
        return f"{sym[:-3]}USDT"
    return None


@dataclass
class FundingQuote:
    """Latest funding rate for a perp contract."""

    exchange: str
    perp_symbol: str
    rate: Decimal
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FundingRateService:
    """Poll public funding endpoints and expose rates for snapshot enrichment."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(getattr(settings, "global_funding_strategy_enabled", True))
        poll = float(getattr(settings, "global_funding_poll_interval_sec", 60.0) or 60.0)
        self._poll_s = max(15.0, poll)
        self._exchanges = [
            e.strip().lower()
            for e in str(getattr(settings, "global_funding_exchanges", "binance") or "binance").split(",")
            if e.strip()
        ]
        symbols = [
            s.strip().upper().replace("-", "").replace("/", "")
            for s in str(settings.market_data_symbols or "").split(",")
            if s.strip()
        ]
        self._perp_symbols = sorted(
            {p for s in symbols if (p := perp_symbol_for(s)) is not None}
        )
        self._rates: dict[tuple[str, str], FundingQuote] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._perp_symbols)

    def rate_for_spot(self, exchange: str, spot_symbol: str) -> Decimal | None:
        perp = perp_symbol_for(spot_symbol)
        if perp is None:
            return None
        quote = self._rates.get((exchange.lower(), perp))
        return quote.rate if quote is not None else None

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "perp_symbols": list(self._perp_symbols),
            "rates": {
                f"{ex}:{sym}": str(q.rate)
                for (ex, sym), q in sorted(self._rates.items())
            },
            "updated": max(
                (q.updated_at.isoformat() for q in self._rates.values()),
                default=None,
            ),
        }

    def import_rates(self, payload: dict[str, str]) -> None:
        for key, raw in payload.items():
            if ":" not in key:
                continue
            exchange, sym = key.split(":", 1)
            try:
                rate = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                continue
            self._rates[(exchange.lower(), sym.upper())] = FundingQuote(
                exchange=exchange.lower(),
                perp_symbol=sym.upper(),
                rate=rate,
            )

    def export_rates(self) -> dict[str, str]:
        return {f"{ex}:{sym}": str(q.rate) for (ex, sym), q in self._rates.items()}

    async def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._running = True
        await self.refresh_once()
        self._task = asyncio.create_task(self._poll_loop(), name="funding-rate-poller")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._poll_s)
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info("FUNDING_POLL_FAILED error=%s", type(exc).__name__)

    async def refresh_once(self) -> None:
        if "binance" in self._exchanges:
            await self._refresh_binance()

    async def _refresh_binance(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for perp in self._perp_symbols:
                try:
                    resp = await client.get(_BINANCE_PREMIUM_INDEX, params={"symbol": perp})
                    resp.raise_for_status()
                    data = resp.json()
                    raw = data.get("lastFundingRate")
                    if raw is None:
                        continue
                    rate = Decimal(str(raw))
                    self._rates[("binance", perp)] = FundingQuote(
                        exchange="binance",
                        perp_symbol=perp,
                        rate=rate,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("BINANCE_FUNDING_FETCH_FAILED symbol=%s error=%s", perp, exc)
