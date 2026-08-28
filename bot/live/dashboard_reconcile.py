"""Rebuild live dashboard KPIs and chart history from exchange trade history."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.live.dashboard_history import clear_history, history_path, record_snapshot
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor, _buy_lot_qty_and_unit

logger = logging.getLogger(__name__)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _audit_fills_since(
    since: datetime,
    *,
    audit_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Filled micro orders from the live audit log (fallback metadata)."""
    path = audit_path or Path("./data/live_audit.jsonl")
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "micro_order_result":
            continue
        payload = row.get("payload") or {}
        if str(payload.get("status") or "").lower() != "filled":
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None or ts < since:
            continue
        qty = Decimal(str(payload.get("filled_quantity") or 0))
        px = Decimal(str(payload.get("average_price") or 0))
        if qty <= 0 or px <= 0:
            continue
        out.append(
            {
                "ts": ts,
                "venue": str(payload.get("venue") or "bitvavo").lower(),
                "symbol": str(payload.get("symbol") or "").upper(),
                "side": str(payload.get("side") or "").lower(),
                "qty": qty,
                "price": px,
                "source": "audit",
            }
        )
    out.sort(key=lambda r: r["ts"])
    return out


async def _fetch_exchange_trades(
    bridge: MicroBudgetLiveExecutor,
    *,
    venue: str,
    base: str,
    since_ms: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    client = bridge._trading_client(venue)
    if client is None:
        return []
    get_ex = getattr(client, "_get_exchange", None)
    if not callable(get_ex):
        return []
    try:
        exchange = await get_ex()
        symbol = f"{base}/{bridge._quote}"
        raw = await exchange.fetch_my_trades(symbol, since=since_ms, limit=limit)
    except Exception:  # noqa: BLE001
        logger.exception("dashboard reconcile fetch trades failed venue=%s base=%s", venue, base)
        return []
    out: list[dict[str, Any]] = []
    for trade in raw or []:
        ts_ms = int(trade.get("timestamp") or 0)
        if not ts_ms:
            continue
        side = str(trade.get("side") or "").lower()
        qty = Decimal(str(trade.get("amount") or 0))
        px = Decimal(str(trade.get("price") or 0))
        if qty <= 0 or px <= 0 or side not in {"buy", "sell"}:
            continue
        fee_info = trade.get("fee") or {}
        out.append(
            {
                "ts": datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC),
                "ts_ms": ts_ms,
                "venue": venue,
                "symbol": f"{base}{bridge._quote}",
                "base": base,
                "side": side,
                "qty": qty,
                "price": px,
                "fee_amt": Decimal(str(fee_info.get("cost") or 0)),
                "fee_cur": str(fee_info.get("currency") or bridge._quote).upper(),
                "source": "exchange",
            }
        )
    out.sort(key=lambda r: r["ts_ms"])
    return out


def _replay_fifo(
    trades: list[dict[str, Any]],
    *,
    since_ms: int,
    quote: str = "EUR",
) -> tuple[Decimal, Decimal, list[dict[str, Any]], int]:
    """Return (realized_before, realized_since, sell_events, fill_count_since)."""
    lots: list[list[Decimal]] = []
    realized_before = Decimal("0")
    realized_since = Decimal("0")
    sell_events: list[dict[str, Any]] = []
    fills_since = 0

    for trade in trades:
        ts_ms = int(trade["ts_ms"])
        side = trade["side"]
        base = str(trade.get("base") or trade.get("symbol", "")[: -len(quote)]).upper()
        amt = trade["qty"]
        px = trade["price"]
        fee_amt = Decimal(str(trade.get("fee_amt") or 0))
        fee_cur = str(trade.get("fee_cur") or quote).upper()

        if side == "buy":
            lot_qty, unit = _buy_lot_qty_and_unit(
                amount=amt,
                price=px,
                fee_amt=fee_amt,
                fee_cur=fee_cur,
                base=base,
                quote=quote,
            )
            lots.append([lot_qty, unit])
            if ts_ms >= since_ms:
                fills_since += 1
            continue

        if fee_cur == base and fee_amt > 0:
            proceeds = amt * px
        else:
            proceeds = amt * px - fee_amt
        cost = Decimal("0")
        rem = amt
        while rem > 0 and lots:
            lot_qty, lot_cost = lots[0]
            take = min(rem, lot_qty)
            cost += take * lot_cost
            lot_qty -= take
            rem -= take
            if lot_qty <= 0:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        pnl = proceeds - cost
        if ts_ms < since_ms:
            realized_before += pnl
        else:
            realized_since += pnl
            fills_since += 1
            sell_events.append(
                {
                    "ts": trade["ts"],
                    "base": base,
                    "venue": trade.get("venue", ""),
                    "qty": str(amt),
                    "price": str(px),
                    "pnl_eur": str(pnl.quantize(Decimal("0.01"))),
                    "realized_cum_eur": str(
                        (realized_since).quantize(Decimal("0.01"))
                    ),
                }
            )
    return realized_before, realized_since, sell_events, fills_since


async def reconcile_dashboard_since(
    bridge: MicroBudgetLiveExecutor,
    since: datetime,
    *,
    seed_days: int = 14,
    history_path_override: Path | None = None,
) -> dict[str, Any]:
    """Rebuild realized KPIs + chart history from exchange fills since ``since``."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    since_ms = int(since.timestamp() * 1000)
    seed_ms = since_ms - max(1, seed_days) * 24 * 3600 * 1000

    venues = sorted(getattr(bridge, "_execute_venues", None) or {"bitvavo"})
    bases: set[str] = set()
    for venue in venues:
        for bal in bridge._venue_raw_balances.get(venue) or []:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if asset and asset not in {bridge._quote, *bridge._exclude_bases}:
                if bridge._allowed_bases is None or asset in bridge._allowed_bases:
                    bases.add(asset)
    if not bases:
        bases = {"SOL", "ADA", "NEAR", "DOT", "XRP", "LINK", "ATOM", "INJ", "APT"}

    all_trades: list[dict[str, Any]] = []
    per_symbol: dict[str, int] = {}
    for venue in venues:
        for base in sorted(bases):
            rows = await _fetch_exchange_trades(
                bridge, venue=venue, base=base, since_ms=seed_ms
            )
            if rows:
                per_symbol[f"{venue}:{base}"] = len(rows)
                all_trades.extend(rows)
    all_trades.sort(key=lambda r: r["ts_ms"])

    realized_before, realized_since, sell_events, fills_since = _replay_fifo(
        all_trades, since_ms=since_ms, quote=bridge._quote
    )

    await bridge.refresh_portfolio_value()
    portfolio_now = bridge.portfolio_value_eur or Decimal("0")
    portfolio_start = portfolio_now - realized_since

    bridge.realized_trade_pnl_eur = realized_since
    bridge.session_start_realized_eur = Decimal("0")
    bridge.starting_portfolio_eur = portfolio_start
    bridge.session_live_transaction_count = fills_since
    bridge.session_live_fill_count = fills_since
    bridge.live_transaction_count = fills_since
    bridge.live_fill_count = fills_since
    bridge._session_started_ms = since_ms
    bridge.skips.clear()
    bridge.set_buys_blocked(False)
    bridge.set_underwater_venue_blocks(set())

    hist_path = history_path_override or history_path()
    clear_history(path=hist_path)
    points_written = 0
    baseline = {
        "t": since.isoformat(),
        "running": "1",
        "portfolio_eur": str(portfolio_start.quantize(Decimal("0.01"))),
        "realized_pnl_eur": "0",
        "unrealized_eur": "0",
        "winnable_eur": "0",
        "session_pnl_eur": "0",
    }
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    with hist_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(baseline, separators=(",", ":")) + "\n")
        points_written += 1
        cum = Decimal("0")
        for ev in sell_events:
            cum = Decimal(str(ev["realized_cum_eur"]))
            port = portfolio_start + cum
            row = {
                "t": ev["ts"].isoformat(),
                "running": "1",
                "portfolio_eur": str(port.quantize(Decimal("0.01"))),
                "realized_pnl_eur": str(cum),
                "unrealized_eur": "0",
                "winnable_eur": "0",
                "session_pnl_eur": str(cum),
            }
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            points_written += 1

    record_snapshot(
        {
            "session": {
                "running": True,
                "portfolio_value_eur": str(portfolio_now),
                "realized_trade_pnl_eur": str(realized_since),
                "starting_portfolio_eur": str(portfolio_start),
                "bridge": bridge.snapshot_bridge(),
            }
        },
        path=hist_path,
        force=True,
    )
    points_written += 1

    bridge.persist_runtime_state()
    audit_fills = _audit_fills_since(since)
    logger.info(
        "DASHBOARD_RECONCILE since=%s realized=%s fills=%s history_points=%s",
        since.isoformat(),
        realized_since,
        fills_since,
        points_written,
    )
    return {
        "ok": True,
        "since": since.isoformat(),
        "realized_since_eur": str(realized_since.quantize(Decimal("0.01"))),
        "realized_before_eur": str(realized_before.quantize(Decimal("0.01"))),
        "portfolio_now_eur": str(portfolio_now),
        "portfolio_start_eur": str(portfolio_start.quantize(Decimal("0.01"))),
        "fills_since": fills_since,
        "audit_fills_since": len(audit_fills),
        "sell_events": sell_events,
        "symbols_replayed": per_symbol,
        "history_points": points_written,
    }
