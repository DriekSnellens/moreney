"""Closed Bitvavo round-trips that never landed on a sleeve ledger.

A manual buy and sell (Bitvavo UI) still moves the EUR balance, so equity
changes, but period PnL only sums ledger exits. These helpers turn account
history into the missing exit rows. Quantity already booked on any sleeve
is skipped, and leftover dust (staking crumbs) under the minimum notional
is ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class _Lot:
    qty: float
    quote_eur: float
    fee_eur: float


@dataclass(frozen=True)
class _Side:
    ts: datetime
    side: str
    base: str
    qty: float
    quote_eur: float
    fee_eur: float
    price: float


def _f(raw: Any) -> float:
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ts(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_account_history(items: Sequence[Mapping[str, Any]]) -> list[_Side]:
    """Normalize Bitvavo account-history rows to base fills against EUR."""
    out: list[_Side] = []
    for raw in items:
        side = str(raw.get("type") or raw.get("side") or "").strip().lower()
        if side not in {"buy", "sell"}:
            continue
        ts = _ts(raw.get("executedAt") or raw.get("ts") or raw.get("timestamp"))
        if ts is None:
            continue
        fee = _f(raw.get("feesAmount") if "feesAmount" in raw else raw.get("fee_eur"))
        fee_ccy = str(raw.get("feesCurrency") or "EUR").upper()
        if fee_ccy not in {"EUR", ""}:
            fee = 0.0
        if side == "buy":
            base = str(raw.get("receivedCurrency") or raw.get("base") or "").upper()
            quote_ccy = str(raw.get("sentCurrency") or "EUR").upper()
            qty = _f(raw.get("receivedAmount") if "receivedAmount" in raw else raw.get("qty"))
            quote = _f(raw.get("sentAmount") if "sentAmount" in raw else raw.get("quote_eur"))
        else:
            base = str(raw.get("sentCurrency") or raw.get("base") or "").upper()
            quote_ccy = str(raw.get("receivedCurrency") or "EUR").upper()
            qty = _f(raw.get("sentAmount") if "sentAmount" in raw else raw.get("qty"))
            quote = _f(raw.get("receivedAmount") if "receivedAmount" in raw else raw.get("quote_eur"))
        if not base or base == "EUR" or quote_ccy not in {"EUR", ""}:
            continue
        if qty <= 0 or quote <= 0:
            continue
        out.append(
            _Side(
                ts=ts,
                side=side,
                base=base,
                qty=qty,
                quote_eur=quote,
                fee_eur=max(0.0, fee),
                price=_f(raw.get("priceAmount") or raw.get("price")),
            )
        )
    out.sort(key=lambda row: row.ts)
    return out


def booked_exit_qty(paths: Sequence[str | Path | None]) -> dict[str, float]:
    """Sum exit quantity already written to sleeve ledgers, per base."""
    out: dict[str, float] = {}
    for path in paths:
        if not path:
            continue
        p = Path(path)
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("event") or "") != "exit":
                continue
            base = str(row.get("base") or "").upper()
            if not base:
                continue
            qty = _f(row.get("quantity") if row.get("quantity") is not None else row.get("qty"))
            if qty <= 0:
                continue
            out[base] = out.get(base, 0.0) + qty
    return out


def ledger_exit_net_eur(
    paths: Sequence[str | Path | None],
    *,
    since: datetime | None = None,
) -> float:
    """Sum live exit ``net_eur`` after ``since`` (a dashboard reset).

    Paper rows (``dry_run`` true) stay out. Used when a fill was written to
    the ledger by another process and the in-memory realized counter lagged.
    """
    since_utc = since.astimezone(UTC) if since is not None else None
    total = 0.0
    for path in paths:
        if not path:
            continue
        p = Path(path)
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("event") or "") != "exit":
                continue
            if row.get("dry_run") is True:
                continue
            ts = _ts(row.get("ts"))
            if since_utc is not None and (ts is None or ts < since_utc):
                continue
            total += _f(row.get("net_eur"))
    return round(total, 2)


def ledger_reset_ts(paths: Sequence[str | Path | None]) -> datetime | None:
    """Latest ``dashboard_reset`` timestamp across sleeve ledgers."""
    latest: datetime | None = None
    for path in paths:
        if not path:
            continue
        p = Path(path)
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if "dashboard_reset" not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("event") or "") != "dashboard_reset":
                continue
            ts = _ts(row.get("ts"))
            if ts is not None and (latest is None or ts > latest):
                latest = ts
    return latest


def unbooked_roundtrip_exits(
    items: Sequence[Mapping[str, Any]],
    booked_qty: Mapping[str, float] | None = None,
    *,
    min_proceeds_eur: float = 5.0,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """FIFO-match buys to later sells and emit exits the ledgers do not have.

    ``booked_qty`` is base → quantity already stored as an exit. That quantity
    consumes the sell (and its buy lots) without emitting a second row.
    Fills before ``since`` (the dashboard reset) are ignored. A sell that is
    not covered by a buy still inside the history page is skipped, so a missing
    cost basis cannot be booked as pure profit. Dust under ``min_proceeds_eur``
    is ignored.
    """
    booked = {str(k).upper(): float(v) for k, v in (booked_qty or {}).items()}
    lots: dict[str, list[_Lot]] = {}
    exits: list[dict[str, Any]] = []
    since_utc = since.astimezone(UTC) if since is not None else None
    for fill in parse_account_history(items):
        if since_utc is not None and fill.ts < since_utc:
            continue
        book = lots.setdefault(fill.base, [])
        if fill.side == "buy":
            book.append(_Lot(qty=fill.qty, quote_eur=fill.quote_eur, fee_eur=fill.fee_eur))
            continue
        left = fill.qty
        already = min(left, max(0.0, booked.get(fill.base, 0.0)))
        if already > 0:
            booked[fill.base] = max(0.0, booked.get(fill.base, 0.0) - already)
            _consume(book, already)
            left -= already
        if left <= 1e-8:
            continue
        taken = _consume(book, left)
        matched_qty = sum(lot.qty for lot in taken)
        # Coins with no buy (staking) only count when the whole remainder is
        # large enough to be a real fill. Sub-minimum leftovers stay out.
        proceeds_gross = fill.quote_eur * (left / fill.qty) if fill.qty else 0.0
        fee_sell = fill.fee_eur * (left / fill.qty) if fill.qty else 0.0
        if proceeds_gross - fee_sell < min_proceeds_eur:
            continue
        if matched_qty < left * 0.95:
            continue
        cost = sum(lot.quote_eur + lot.fee_eur for lot in taken)
        # Unmatched qty has no cost basis.
        net = (proceeds_gross - fee_sell) - cost
        buy_quote = sum(lot.quote_eur for lot in taken)
        buy_fee = sum(lot.fee_eur for lot in taken)
        qty = left
        entry = (buy_quote / matched_qty) if matched_qty > 1e-12 else 0.0
        exit_px = fill.price if fill.price > 0 else (proceeds_gross / qty if qty else 0.0)
        exits.append(
            {
                "event": "exit",
                "base": fill.base,
                "role": "alt",
                "venue": "bitvavo",
                "dry_run": False,
                "notional_eur": round(buy_quote + buy_fee, 2),
                "quantity": qty,
                "entry_price": entry,
                "exit_price": exit_px,
                "fee_eur": round(buy_fee + fee_sell, 4),
                "net_eur": round(net, 2),
                "reason": "manual_external",
                "detail": "untracked_roundtrip",
                "ts": fill.ts.isoformat(),
            }
        )
    return exits


def _consume(book: list[_Lot], qty: float) -> list[_Lot]:
    """Take ``qty`` out of ``book`` in FIFO order. Mutates ``book``."""
    need = max(0.0, qty)
    taken: list[_Lot] = []
    while need > 1e-12 and book:
        lot = book[0]
        if lot.qty <= need + 1e-12:
            taken.append(lot)
            need -= lot.qty
            book.pop(0)
            continue
        frac = need / lot.qty
        taken.append(
            _Lot(qty=need, quote_eur=lot.quote_eur * frac, fee_eur=lot.fee_eur * frac)
        )
        book[0] = _Lot(
            qty=lot.qty - need,
            quote_eur=lot.quote_eur * (1.0 - frac),
            fee_eur=lot.fee_eur * (1.0 - frac),
        )
        need = 0.0
    return taken
