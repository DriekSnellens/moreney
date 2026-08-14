"""Persistent paper-trading state.

PostgreSQL/SQLAlchemy remains the preferred source of truth when a DB is
available. A JSON file store guarantees restart survival without requiring
Postgres for local paper sessions and tests.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from bot.core.config import Settings
from bot.portfolio.models import AssetBalance, Fill, Order, PortfolioState, PortfolioStats, PositionState
from bot.portfolio.persistence import InMemoryPaperStore
from bot.portfolio.portfolio import PaperPortfolio
from bot.paper.tracker import PerformanceTracker

logger = logging.getLogger(__name__)


class PaperTradingStore:
    """Combines in-memory mirrors with optional durable JSON persistence."""

    def __init__(
        self,
        settings: Settings,
        *,
        persist_path: str | Path | None = None,
    ) -> None:
        self._settings = settings
        self._path = Path(persist_path or settings.paper_persist_path)
        self.memory = InMemoryPaperStore()
        self.session_running: bool = False
        self.session_started_at: str | None = None
        self.errors: list[str] = []
        self.runtime_seconds: float = 0.0

    def save_order(self, order: Order) -> None:
        self.memory.save_order(order)

    def save_fill(self, fill: Fill) -> None:
        self.memory.save_fill(fill)

    def save_portfolio(self, portfolio: PaperPortfolio) -> None:
        self.memory.save_portfolio(portfolio)

    def save_daily_stats(self, portfolio: PaperPortfolio) -> None:
        self.memory.save_daily_stats(portfolio)

    def persist(
        self,
        *,
        portfolio: PaperPortfolio,
        tracker: PerformanceTracker,
        session_running: bool,
        session_started_at: str | None,
        errors: list[str],
        runtime_seconds: float,
        decision_log: list[dict] | None = None,
    ) -> None:
        """Write full paper session state to disk (survives restart)."""
        self.session_running = session_running
        self.session_started_at = session_started_at
        self.errors = list(errors)
        self.runtime_seconds = runtime_seconds
        self.save_portfolio(portfolio)
        self.save_daily_stats(portfolio)

        payload = {
            "version": 1,
            "execution_mode": "paper",
            "real_orders_placed": 0,
            "withdrawals": 0,
            "leverage": 0,
            "session_running": session_running,
            "session_started_at": session_started_at,
            "errors": errors,
            "runtime_seconds": runtime_seconds,
            "portfolio": _portfolio_dict(portfolio),
            "tracker": tracker.export_state(),
            "decision_log": decision_log or [],
            "orders": {
                str(k): _record_dict(v) for k, v in self.memory.orders.items()
            },
            "fills": {
                str(k): _record_dict(v) for k, v in self.memory.fills.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._path)
        logger.info("PAPER_STATE_PERSISTED path=%s equity=%s", self._path, portfolio.state.total_equity)

    def load(
        self,
        settings: Settings | None = None,
    ) -> tuple[PaperPortfolio, PerformanceTracker, dict[str, Any]] | None:
        """Reload portfolio + tracker from disk. Returns None if no state file."""
        cfg = settings or self._settings
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("PAPER_STATE_LOAD_FAILED path=%s error=%s", self._path, exc)
            return None

        portfolio = PaperPortfolio(cfg)
        pdata = raw.get("portfolio") or {}
        if pdata:
            balances = {
                asset: AssetBalance(
                    asset=asset,
                    available=Decimal(str(data["available"])),
                    reserved=Decimal(str(data.get("reserved", "0"))),
                )
                for asset, data in (pdata.get("balances") or {}).items()
            }
            positions = {
                symbol: PositionState(
                    symbol=symbol,
                    quantity=Decimal(str(data["quantity"])),
                    average_entry_price=Decimal(str(data.get("average_entry_price", "0"))),
                    realized_pnl=Decimal(str(data.get("realized_pnl", "0"))),
                    fees_paid=Decimal(str(data.get("fees_paid", "0"))),
                )
                for symbol, data in (pdata.get("positions") or {}).items()
            }
            stats_raw = pdata.get("stats") or {}
            state = PortfolioState(
                balances=balances,
                positions=positions,
                quote_asset=pdata.get("quote_asset", cfg.paper_quote_asset),
                stats=PortfolioStats(
                    realized_pnl=Decimal(str(stats_raw.get("realized_pnl", "0"))),
                    unrealized_pnl=Decimal(str(stats_raw.get("unrealized_pnl", "0"))),
                    fees_paid=Decimal(str(stats_raw.get("fees_paid", "0"))),
                    total_trading_volume=Decimal(str(stats_raw.get("total_trading_volume", "0"))),
                    number_of_trades=int(stats_raw.get("number_of_trades", 0)),
                    winning_trades=int(stats_raw.get("winning_trades", 0)),
                    losing_trades=int(stats_raw.get("losing_trades", 0)),
                    peak_equity=Decimal(str(stats_raw.get("peak_equity", pdata.get("equity", "0")))),
                    current_drawdown=Decimal(str(stats_raw.get("current_drawdown", "0"))),
                    maximum_drawdown=Decimal(str(stats_raw.get("maximum_drawdown", "0"))),
                ),
                mark_prices={
                    k: Decimal(str(v)) for k, v in (pdata.get("mark_prices") or {}).items()
                },
            )
            processed = {str(x) for x in (pdata.get("processed_fill_ids") or [])}
            portfolio.load_state(state, processed_fill_ids=processed)
            portfolio.load_venue_ledger(pdata.get("venue_ledger"))

        starting = Decimal(str((raw.get("tracker") or {}).get("starting_equity", cfg.paper_starting_eur)))
        tracker = PerformanceTracker(starting_equity=starting)
        if raw.get("tracker"):
            tracker.import_state(raw["tracker"])
        tracker.sync_portfolio(portfolio)

        self.session_running = bool(raw.get("session_running", False))
        self.session_started_at = raw.get("session_started_at")
        self.errors = list(raw.get("errors") or [])
        self.runtime_seconds = float(raw.get("runtime_seconds") or 0)

        meta = {
            "session_running": self.session_running,
            "session_started_at": self.session_started_at,
            "errors": self.errors,
            "runtime_seconds": self.runtime_seconds,
            "decision_log": list(raw.get("decision_log") or []),
        }
        logger.info(
            "PAPER_STATE_LOADED path=%s equity=%s opportunities=%s",
            self._path,
            portfolio.state.total_equity,
            tracker.snapshot().total_opportunities,
        )
        return portfolio, tracker, meta

    def clear(self) -> None:
        self.memory = InMemoryPaperStore()
        self.session_running = False
        self.session_started_at = None
        self.errors = []
        self.runtime_seconds = 0.0
        if self._path.exists():
            self._path.unlink()


def _portfolio_dict(portfolio: PaperPortfolio) -> dict[str, Any]:
    state = portfolio.state
    return {
        "quote_asset": state.quote_asset,
        "equity": str(state.total_equity),
        "balances": {
            k: {"available": str(v.available), "reserved": str(v.reserved)}
            for k, v in state.balances.items()
        },
        "positions": {
            k: {
                "quantity": str(v.quantity),
                "average_entry_price": str(v.average_entry_price),
                "realized_pnl": str(v.realized_pnl),
                "fees_paid": str(v.fees_paid),
            }
            for k, v in state.positions.items()
        },
        "stats": state.stats.model_dump(mode="json"),
        "mark_prices": {k: str(v) for k, v in state.mark_prices.items()},
            "processed_fill_ids": sorted(portfolio.accounting.processed_fill_ids),
            "venue_ledger": (
                portfolio.venue_ledger.export() if portfolio.venue_ledger is not None else None
            ),
    }


def _record_dict(record: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for col in record.__table__.columns:  # type: ignore[attr-defined]
        val = getattr(record, col.name)
        if isinstance(val, UUID):
            data[col.name] = str(val)
        elif isinstance(val, Decimal):
            data[col.name] = str(val)
        else:
            data[col.name] = val
    return data
