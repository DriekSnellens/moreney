#!/usr/bin/env python3
"""Sell enough Bitvavo SUI → EUR to fund the €20k momentum desk book.

Loads the same env files as moreney-live@micro. Slices market sells to
limit impact, then prints post balances.
"""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.models import OrderRequest
from bot.exchanges.factory import create_exchange_client

ROOT = Path("/opt/moreney")
ENV_FILES = (
    ROOT / ".env",
    ROOT / "env" / "bitvavo-observe.env",
    ROOT / "env" / "live-micro.env",
)
TARGET_BITVAVO_EUR = 20_050.0
SLICE_EUR = 3_500.0
MAX_SLICES = 12


def _load_env() -> None:
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            # Later files override (same order as systemd EnvironmentFile stack).
            os.environ[key] = val


def _eur_and_sui(snap) -> tuple[float, float]:
    eur = sui = 0.0
    for bal in snap.balances:
        asset = str(bal.asset).upper()
        free = float(getattr(bal, "free", None) or bal.total or 0.0)
        if asset == "EUR":
            eur = free
        elif asset == "SUI":
            sui = free
    return eur, sui


async def main() -> int:
    _load_env()
    settings = Settings(exchange_name="bitvavo")
    client = create_exchange_client(settings, enable_trading=True)
    print("trading_enabled", getattr(client, "_enable_trading", None))

    snap = await client.get_balances()
    eur0, sui0 = _eur_and_sui(snap)
    ticker = await client._call(  # noqa: SLF001
        (await client._get_exchange()).fetch_ticker, "SUI/EUR"  # noqa: SLF001
    )
    px = float(ticker.get("last") or ticker.get("bid") or 0.0)
    print(f"before EUR={eur0:.2f} SUI={sui0:.4f} px={px:.5f} mark€={sui0 * px:.2f}")

    need_eur = max(0.0, TARGET_BITVAVO_EUR - eur0)
    if need_eur <= 25.0:
        print("already funded; nothing to sell")
        return 0
    if px <= 0 or sui0 <= 0:
        print("no SUI or no price")
        return 2

    # Slight overshoot for fees / slippage.
    sell_qty = min(sui0, (need_eur * 1.004) / px)
    print(f"target_raise€={need_eur:.2f} sell_qty={sell_qty:.4f} (~€{sell_qty * px:.2f})")

    remaining = sell_qty
    filled_total = 0.0
    notional_total = 0.0
    for i in range(MAX_SLICES):
        if remaining * px < 25.0:
            break
        slice_qty = min(remaining, SLICE_EUR / px)
        # Refresh bid for sizing sanity.
        ex = await client._get_exchange()  # noqa: SLF001
        t = await client._call(ex.fetch_ticker, "SUI/EUR")  # noqa: SLF001
        bid = float(t.get("bid") or t.get("last") or px)
        req = OrderRequest(
            opportunity_id=uuid4(),
            symbol="SUI-EUR",
            side=OpportunitySide.SELL,
            quantity=Decimal(str(round(slice_qty, 6))),
            limit_price=None,  # market
            client_order_id=f"sui-liq-{int(time.time())}-{i}",
            metadata={"reason": "fund_desk_book", "slice": i},
        )
        print(f"slice{i}: market sell {float(req.quantity):.4f} SUI (bid≈{bid:.5f})")
        result = await client.place_order(req)
        print(
            f"  status={result.status} filled={result.filled_quantity} "
            f"avg={result.average_price} msg={result.message}"
        )
        fq = float(result.filled_quantity or 0.0)
        ap = float(result.average_price or bid)
        if fq <= 0:
            # Fallback: aggressive limit at bid * 0.998
            req2 = OrderRequest(
                opportunity_id=uuid4(),
                symbol="SUI-EUR",
                side=OpportunitySide.SELL,
                quantity=Decimal(str(round(slice_qty, 6))),
                limit_price=Decimal(str(round(bid * 0.998, 5))),
                client_order_id=f"sui-liq-l-{int(time.time())}-{i}",
                metadata={"reason": "fund_desk_book_limit", "slice": i},
            )
            print(f"  fallback limit @ {req2.limit_price}")
            result = await client.place_order(req2)
            print(
                f"  status={result.status} filled={result.filled_quantity} "
                f"avg={result.average_price} msg={result.message}"
            )
            fq = float(result.filled_quantity or 0.0)
            ap = float(result.average_price or bid)
            if fq <= 0:
                print("slice failed; stopping")
                break
        filled_total += fq
        notional_total += fq * ap
        remaining -= fq
        await asyncio.sleep(0.8)
        snap = await client.get_balances()
        eur, sui = _eur_and_sui(snap)
        print(f"  running EUR={eur:.2f} SUI={sui:.4f}")
        if eur >= TARGET_BITVAVO_EUR:
            break

    snap = await client.get_balances()
    eur1, sui1 = _eur_and_sui(snap)
    print(
        f"done sold≈{filled_total:.4f} SUI for ≈€{notional_total:.2f} | "
        f"after EUR={eur1:.2f} SUI={sui1:.4f}"
    )
    close = getattr(client, "close", None) or getattr(client, "aclose", None)
    if callable(close):
        maybe = close()
        if asyncio.iscoroutine(maybe):
            await maybe
    return 0 if eur1 >= TARGET_BITVAVO_EUR - 50 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
