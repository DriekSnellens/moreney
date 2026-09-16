"""Free public equity quotes: Nasdaq (US live bid/ask) + Yahoo chart (EU last).

No API key. Unofficial public JSON endpoints — not a licensed SIP/NBBO feed.
Polls on an interval; suitable for paper mean-reversion, not HFT.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import MarketSnapshot

logger = logging.getLogger(__name__)

_NASDAQ_QUOTE = "https://api.nasdaq.com/api/quote/{ticker}/info"
_YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
_ZERO = Decimal("0")
_DEFAULT_SIZE = Decimal("100")
_SYNTHETIC_SPREAD_BPS = Decimal("2")
_KNOWN_ETFS = {
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "VOO",
    "VTI",
    "IVV",
    "GLD",
    "SLV",
    "TLT",
    "HYG",
    "EEM",
    "EFA",
    "XLK",
    "XLF",
    "XLE",
    "ARKK",
    "SOXX",
    "SMH",
}


def parse_equity_symbols(raw: str) -> list[str]:
    """Normalize configured symbols, e.g. ``SPY.US,AAPL.US,SAP.DE``."""
    out: list[str] = []
    seen: set[str] = set()
    for part in str(raw or "").split(","):
        text = part.strip().upper()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def yahoo_ticker(internal: str) -> str:
    """Map internal ``AAPL.US`` / ``SAP.DE`` to Yahoo chart tickers."""
    sym = internal.strip().upper()
    if sym.endswith(".US"):
        return sym[:-3]
    return sym


def nasdaq_ticker(internal: str) -> str | None:
    """US-listed ticker for the Nasdaq public quote API, or None for non-US."""
    sym = internal.strip().upper()
    if "." in sym and not sym.endswith(".US"):
        return None
    if sym.endswith(".US"):
        return sym[:-3]
    return sym


def parse_money(raw: object) -> Decimal | None:
    """Parse ``$304.90`` / ``184.26`` into Decimal."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text in {"N/A", "--", "null", "None"}:
        return None
    cleaned = (
        text.replace("$", "")
        .replace("€", "")
        .replace(",", "")
        .replace(" ", "")
    )
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


def parse_size(raw: object) -> Decimal:
    value = parse_money(raw)
    if value is None or value <= 0:
        return _DEFAULT_SIZE
    return value


@dataclass
class EquityQuote:
    """Latest public quote for one internal equity symbol."""

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    bid_size: Decimal = _DEFAULT_SIZE
    ask_size: Decimal = _DEFAULT_SIZE
    source: str = "yahoo"
    exchange: str = "yahoo"
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    realtime: bool = False

    def to_snapshot(self) -> MarketSnapshot:
        book = OrderBook(
            symbol=self.symbol,
            bids=[OrderBookLevel(price=self.bid, amount=self.bid_size)],
            asks=[OrderBookLevel(price=self.ask, amount=self.ask_size)],
            timestamp=self.updated_at,
            metadata={"source": self.source, "asset_class": "equity"},
        )
        return MarketSnapshot(
            symbol=self.symbol,
            bid=self.bid,
            ask=self.ask,
            last=self.last,
            order_book=book,
            exchange=self.exchange,
            timestamp=self.updated_at,
            metadata={
                "asset_class": "equity",
                "source": self.source,
                "realtime": "true" if self.realtime else "false",
                "stub": "false",
            },
        )

    def export(self) -> dict[str, str]:
        return {
            "bid": str(self.bid),
            "ask": str(self.ask),
            "last": str(self.last),
            "bid_size": str(self.bid_size),
            "ask_size": str(self.ask_size),
            "source": self.source,
            "exchange": self.exchange,
            "updated_at": self.updated_at.isoformat(),
            "realtime": "true" if self.realtime else "false",
        }


def quote_from_export(symbol: str, payload: dict[str, str]) -> EquityQuote | None:
    bid = parse_money(payload.get("bid"))
    ask = parse_money(payload.get("ask"))
    last = parse_money(payload.get("last")) or bid or ask
    if bid is None or ask is None or last is None:
        return None
    ts_raw = payload.get("updated_at")
    try:
        updated = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now(UTC)
    except ValueError:
        updated = datetime.now(UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return EquityQuote(
        symbol=symbol.upper(),
        bid=bid,
        ask=ask,
        last=last,
        bid_size=parse_size(payload.get("bid_size")),
        ask_size=parse_size(payload.get("ask_size")),
        source=str(payload.get("source") or "yahoo"),
        exchange=str(payload.get("exchange") or "yahoo"),
        updated_at=updated,
        realtime=str(payload.get("realtime") or "").lower() in {"1", "true", "yes"},
    )


def _synthetic_spread(last: Decimal) -> tuple[Decimal, Decimal]:
    half = last * _SYNTHETIC_SPREAD_BPS / Decimal("10000") / Decimal("2")
    if half <= 0:
        half = Decimal("0.01")
    return last - half, last + half


class EquityQuoteService:
    """Poll Nasdaq (US) and Yahoo (EU/fallback) into in-memory equity snapshots."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._enabled = bool(getattr(settings, "global_equity_enabled", False))
        poll = float(getattr(settings, "global_equity_poll_interval_sec", 15.0) or 15.0)
        self._poll_s = max(10.0, poll)
        self._symbols = parse_equity_symbols(
            str(getattr(settings, "global_equity_symbols", "") or "")
        )
        self._quotes: dict[str, EquityQuote] = {}
        self._nasdaq_class: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._client = client
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._symbols)

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    def is_equity_symbol(self, symbol: str) -> bool:
        return symbol.strip().upper() in {s.upper() for s in self._symbols}

    def snapshot_for(self, symbol: str) -> MarketSnapshot | None:
        quote = self._quotes.get(symbol.strip().upper())
        return quote.to_snapshot() if quote is not None else None

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "symbols": list(self._symbols),
            "quotes": {
                sym: {
                    "last": str(q.last),
                    "bid": str(q.bid),
                    "ask": str(q.ask),
                    "source": q.source,
                    "realtime": q.realtime,
                    "updated_at": q.updated_at.isoformat(),
                }
                for sym, q in sorted(self._quotes.items())
            },
            "updated": max(
                (q.updated_at.isoformat() for q in self._quotes.values()),
                default=None,
            ),
        }

    def export_quotes(self) -> dict[str, dict[str, str]]:
        return {sym: q.export() for sym, q in self._quotes.items()}

    def import_quotes(self, payload: dict[str, dict[str, str]]) -> None:
        for sym, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            quote = quote_from_export(str(sym), {str(k): str(v) for k, v in raw.items()})
            if quote is not None:
                self._quotes[quote.symbol] = quote

    async def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._running = True
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0, headers=_HEADERS)
        await self.refresh_once()
        self._task = asyncio.create_task(self._poll_loop(), name="equity-quote-poller")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._poll_s)
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info("EQUITY_POLL_FAILED error=%s", type(exc).__name__)

    async def refresh_once(self) -> None:
        if not self.enabled:
            return
        for symbol in self._symbols:
            try:
                quote = await self._fetch_symbol(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.debug("EQUITY_FETCH_FAILED symbol=%s error=%s", symbol, exc)
                continue
            if quote is not None:
                self._quotes[symbol] = quote

    async def _fetch_symbol(self, symbol: str) -> EquityQuote | None:
        us_ticker = nasdaq_ticker(symbol)
        if us_ticker is not None:
            quote = await self._fetch_nasdaq(symbol, us_ticker)
            if quote is not None:
                return quote
        return await self._fetch_yahoo(symbol)

    async def _fetch_nasdaq(self, internal: str, ticker: str) -> EquityQuote | None:
        classes = []
        cached = self._nasdaq_class.get(ticker)
        if cached:
            classes.append(cached)
        if ticker in _KNOWN_ETFS:
            classes.extend(["etf", "stocks"])
        else:
            classes.extend(["stocks", "etf"])
        ordered: list[str] = []
        for item in classes:
            if item not in ordered:
                ordered.append(item)
        for assetclass in ordered:
            payload = await self._get_json(
                _NASDAQ_QUOTE.format(ticker=ticker),
                params={"assetclass": assetclass},
            )
            quote = self._parse_nasdaq(internal, payload)
            if quote is not None:
                self._nasdaq_class[ticker] = assetclass
                return quote
        return None

    def _parse_nasdaq(self, internal: str, payload: object) -> EquityQuote | None:
        if not isinstance(payload, dict):
            return None
        status = payload.get("status") or {}
        if isinstance(status, dict) and int(status.get("rCode") or 0) >= 400:
            return None
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return None
        primary = data.get("primaryData") or {}
        if not isinstance(primary, dict):
            return None
        last = parse_money(primary.get("lastSalePrice"))
        bid = parse_money(primary.get("bidPrice"))
        ask = parse_money(primary.get("askPrice"))
        if last is None and bid is None and ask is None:
            return None
        if last is None:
            last = ((bid or _ZERO) + (ask or _ZERO)) / Decimal("2")
        if last <= 0:
            return None
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            bid, ask = _synthetic_spread(last)
        realtime = bool(primary.get("isRealTime"))
        return EquityQuote(
            symbol=internal,
            bid=bid,
            ask=ask,
            last=last,
            bid_size=parse_size(primary.get("bidSize")),
            ask_size=parse_size(primary.get("askSize")),
            source="nasdaq",
            exchange="nasdaq",
            realtime=realtime,
        )

    async def _fetch_yahoo(self, internal: str) -> EquityQuote | None:
        ticker = yahoo_ticker(internal)
        payload = await self._get_json(
            _YAHOO_CHART.format(ticker=ticker),
            params={"interval": "1m", "range": "1d"},
        )
        if not isinstance(payload, dict):
            return None
        results = ((payload.get("chart") or {}).get("result") or []) if payload else []
        if not results or not isinstance(results[0], dict):
            return None
        meta = results[0].get("meta") or {}
        last = parse_money(meta.get("regularMarketPrice"))
        if last is None:
            return None
        bid = parse_money(meta.get("bid"))
        ask = parse_money(meta.get("ask"))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            bid, ask = _synthetic_spread(last)
        return EquityQuote(
            symbol=internal,
            bid=bid,
            ask=ask,
            last=last,
            source="yahoo",
            exchange="yahoo",
            realtime=False,
        )

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> object:
        client = self._client
        if client is None:
            async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as tmp:
                resp = await tmp.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
