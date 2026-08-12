"""Real public-market-data paper test.

HARD SAFETY:
* EXECUTION_MODE=paper
* Never places real orders
* Never fabricates opportunities or forces trades
* Zero opportunities / zero trades is an acceptable honest result
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# Force paper safety before importing app settings.
os.environ["EXECUTION_MODE"] = "paper"
os.environ["PAPER_TRADING_ENABLED"] = "true"
os.environ["PAPER_AUTO_START"] = "false"
os.environ["PAPER_STARTING_EUR"] = "200"
os.environ["PAPER_CYCLE_INTERVAL_MS"] = "1000"
os.environ["PAPER_PERSIST_PATH"] = str(
    Path(__file__).resolve().parents[1] / "data" / "paper_live_test_state.json"
)
os.environ["MARKET_DATA_EXCHANGES"] = "binance,kraken,coinbase,bitvavo"
os.environ["MARKET_DATA_SYMBOLS"] = "BTCEUR,BTCUSDT"
os.environ["MAX_MARKET_DATA_AGE_MS"] = "5000"

from bot.core.config import get_settings
from bot.market_data.service import MarketDataService
from bot.paper.runner import PaperRunner
from bot.paper.store import PaperTradingStore
from bot.risk.risk_engine import RiskEngine

RUNTIME_SECONDS = float(os.environ.get("PAPER_LIVE_TEST_SECONDS", "45"))


async def main() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.execution_mode.value == "paper"
    assert settings.paper_auto_start is False
    assert settings.paper_starting_eur == 200.0

    # Fresh session for the live observation (do not inherit prior test equity).
    persist = Path(settings.paper_persist_path)
    if persist.exists():
        persist.unlink()

    md = MarketDataService(settings, start_websockets=True)
    risk = RiskEngine(settings)
    store = PaperTradingStore(settings)
    runner = PaperRunner(settings, market_data=md, risk_engine=risk, store=store)

    print("=== PHASE 1: verify market-data connections (PAPER_AUTO_START=false) ===")
    await md.start()
    await asyncio.sleep(8)
    md_status = md.status()
    connected = [ex for ex, h in md_status.items() if h.get("connected")]
    synchronized = [
        ex for ex, h in md_status.items() if h.get("synchronized") and not h.get("stale")
    ]
    print(json.dumps({"connected": connected, "synchronized": synchronized, "raw": md_status}, indent=2, default=str))

    print("=== PHASE 2: manually start paper trading ===")
    started = await runner.start()
    print(json.dumps(started, indent=2, default=str))
    t0 = time.monotonic()
    print(f"=== PHASE 3: observe real public market data for {RUNTIME_SECONDS:.0f}s ===")
    while time.monotonic() - t0 < RUNTIME_SECONDS:
        await asyncio.sleep(5)
        snap = runner.tracker.snapshot()
        print(
            f"[{datetime.now(UTC).isoformat()}] "
            f"equity={snap.current_equity} opps={snap.total_opportunities} "
            f"approved={snap.approved_opportunities} rejected={snap.rejected_opportunities} "
            f"executed={snap.executed_opportunities} trades={snap.trade_count} "
            f"cycles={runner.status()['cycle_count']}"
        )

    await runner.stop()
    await md.stop()

    snap = runner.tracker.snapshot()
    final_md = md.status()
    connected_final = [ex for ex, h in final_md.items() if h.get("connected") or h.get("synchronized")]
    report = {
        "paper_starting_balance": str(snap.starting_equity),
        "current_balance": str(snap.current_equity),
        "opportunities": snap.total_opportunities,
        "approved": snap.approved_opportunities,
        "rejected": snap.rejected_opportunities,
        "executed_trades": snap.trade_count,
        "executed_opportunities": snap.executed_opportunities,
        "gross_pnl": str(snap.gross_pnl),
        "fees": str(snap.fees),
        "slippage": str(snap.slippage),
        "net_pnl": str(snap.net_pnl),
        "win_rate": str(snap.win_rate),
        "max_drawdown": str(snap.maximum_drawdown),
        "connected_exchanges": connected_final,
        "runtime_seconds": runner.runtime_seconds(),
        "errors": runner.errors,
        "REAL_ORDERS_PLACED": 0,
        "WITHDRAWALS": 0,
        "LEVERAGE": 0,
        "EXECUTION_MODE": "PAPER",
    }
    print("=== FINAL PAPER LIVE REPORT ===")
    print(json.dumps(report, indent=2))
    out = Path(__file__).resolve().parents[1] / "data" / "paper_live_test_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
