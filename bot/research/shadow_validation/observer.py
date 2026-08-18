"""Live shadow observer. Simulated execution only. No exchange orders."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from bot.research.execution_realism.config import EXECUTION_REALISM_PRODUCTION_ENABLED
from bot.research.shadow_validation.accumulator import ShadowAccumulator
from bot.research.shadow_validation.artifacts import (
    CompactObservationWriter,
    default_paths,
    write_accumulator_snapshot,
)
from bot.research.shadow_validation.books import CompactL1, L1View, inspect_l1
from bot.research.shadow_validation.detector import detect_signal
from bot.research.shadow_validation.economics import ExpectedEconomics, expected_from_dislocation
from bot.research.shadow_validation.identity import ensure_frozen_identity
from bot.research.shadow_validation.outcomes import classify_observation
from bot.research.shadow_validation.protocol import (
    DEFAULT_RUN_DIR,
    ENTRY_OBSERVE_MS,
    HEDGE_OBSERVE_MS,
    HISTORICAL_FINAL_VALIDATION,
    HORIZON_MS,
    MAX_PENDING,
    PRODUCTION_EXECUTION_ENABLED,
    STRATEGY_DISPLAY_NAME,
    STRATEGY_ID,
    VENUE_A,
    VENUE_B,
)
from bot.research.shadow_validation.report import maybe_write_final
from bot.research.shadow_validation.verdict import decide

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Pending:
    candidate_id: str
    fingerprint: str
    symbol: str
    a_rich: bool
    entry_side: str
    hedge_side: str
    decision_ts_ms: float
    entry_due_ms: float
    hedge_due_ms: float
    horizon_due_ms: float
    decision_entry: CompactL1
    decision_hedge: CompactL1
    expected: ExpectedEconomics
    later_entry: L1View | None = None
    later_hedge: L1View | None = None


class ShadowPaperObserver:
    """Observe frozen CVD on the live book path. Never places orders."""

    alters_execution = False
    execution_enabled = False

    def __init__(
        self,
        *,
        run_dir: str | None = None,
        git_commit_override: str | None = None,
        now_ms: float | None = None,
    ) -> None:
        if PRODUCTION_EXECUTION_ENABLED or EXECUTION_REALISM_PRODUCTION_ENABLED:
            raise RuntimeError("shadow observer refuses to start with production execution enabled")
        self.run_dir = run_dir or DEFAULT_RUN_DIR
        identity, invalidated = ensure_frozen_identity(
            run_dir=self.run_dir,
            git_commit_override=git_commit_override,
        )
        self.identity = identity
        self.fingerprint = str(identity["strategy_fingerprint"])
        self.run_fingerprint = str(identity["run_fingerprint"])
        self.invalidated_prior = invalidated
        self.acc = ShadowAccumulator()
        now = now_ms if now_ms is not None else time.time() * 1000.0
        self.acc.run_start_ms = now
        self._pending: dict[str, _Pending] = {}
        self._inflight_symbols: set[str] = set()
        self._seq = 0
        self._final_written = False
        paths = default_paths(self.run_dir)
        self._writer = CompactObservationWriter(paths["observations"])
        self._acc_path = paths["accumulator"]
        self._last_acc_write = 0.0
        self._cycles = 0

    def _view(self, books: dict[str, dict[str, Any]], venue: str, symbol: str, now_ms: float) -> L1View:
        book = (books.get(venue) or {}).get(symbol)
        return inspect_l1(book, venue=venue, symbol=symbol, now_ms=now_ms)

    def process_cycle(
        self,
        books: dict[str, dict[str, Any]],
        *,
        symbols: list[str],
        now_ms: float | None = None,
    ) -> None:
        now = now_ms if now_ms is not None else time.time() * 1000.0
        self._cycles += 1
        self._resolve_due(books, now)
        self._detect_new(books, symbols, now)
        if self._cycles % 32 == 0:
            write_accumulator_snapshot(self._acc_path, self.acc.snapshot(now_ms=now, fingerprint=self.fingerprint))
        self._maybe_finalize(now)

    def _resolve_due(self, books: dict[str, dict[str, Any]], now_ms: float) -> None:
        done: list[str] = []
        for cid, pend in self._pending.items():
            if pend.later_entry is None and now_ms >= pend.entry_due_ms:
                pend.later_entry = self._view(books, pend.decision_entry.venue, pend.symbol, now_ms)
            if pend.later_hedge is None and now_ms >= pend.hedge_due_ms:
                pend.later_hedge = self._view(books, pend.decision_hedge.venue, pend.symbol, now_ms)
            if now_ms < pend.horizon_due_ms:
                continue
            future = self._view(books, pend.decision_entry.venue, pend.symbol, now_ms)
            later_entry = pend.later_entry or future
            later_hedge = pend.later_hedge or self._view(
                books, pend.decision_hedge.venue, pend.symbol, now_ms
            )
            result = classify_observation(
                candidate_id=pend.candidate_id,
                strategy_fingerprint=pend.fingerprint,
                signal_time_ms=pend.decision_ts_ms,
                now_ms=now_ms,
                a_rich=pend.a_rich,
                entry_side=pend.entry_side,
                hedge_side=pend.hedge_side,
                decision_entry=pend.decision_entry,
                decision_hedge=pend.decision_hedge,
                later_entry=later_entry,
                later_hedge=later_hedge,
                future_entry=future,
                expected=pend.expected,
                decision_book_age_ms=pend.decision_entry.book_age_ms,
            )
            self.acc.complete(result, expected=pend.expected)
            self._writer.enqueue(result.record)
            done.append(cid)
        for cid in done:
            pend = self._pending.pop(cid)
            self._inflight_symbols.discard(pend.symbol)

    def _detect_new(self, books: dict[str, dict[str, Any]], symbols: list[str], now_ms: float) -> None:
        for symbol in symbols:
            if symbol in self._inflight_symbols:
                continue
            if len(self._pending) >= MAX_PENDING:
                self.acc.skip_pending_full()
                return
            view_a = self._view(books, VENUE_A, symbol, now_ms)
            view_b = self._view(books, VENUE_B, symbol, now_ms)
            if not view_a.ok or not view_b.ok or view_a.l1 is None or view_b.l1 is None:
                continue
            sig = detect_signal(view_a.l1, view_b.l1)
            if sig is None:
                continue
            self._seq += 1
            cid = f"{self.fingerprint[:10]}-{self._seq:08d}"
            expected = expected_from_dislocation(sig.dislocation)
            self.acc.observe_signal()
            self._pending[cid] = _Pending(
                candidate_id=cid,
                fingerprint=self.fingerprint,
                symbol=symbol,
                a_rich=sig.a_rich,
                entry_side=sig.entry_side,
                hedge_side=sig.hedge_side,
                decision_ts_ms=now_ms,
                entry_due_ms=now_ms + ENTRY_OBSERVE_MS,
                hedge_due_ms=now_ms + HEDGE_OBSERVE_MS,
                horizon_due_ms=now_ms + float(HORIZON_MS),
                decision_entry=sig.entry,
                decision_hedge=sig.hedge,
                expected=expected,
            )
            self._inflight_symbols.add(symbol)

    def _maybe_finalize(self, now_ms: float) -> None:
        if self._final_written:
            return
        if not self.acc.sample_complete(now_ms):
            return
        self._writer.flush()
        snap = self.acc.snapshot(now_ms=now_ms, fingerprint=self.fingerprint)
        decision = decide(snap)
        maybe_write_final(
            identity=self.identity,
            snapshot=snap,
            decision=decision,
            run_start_ms=self.acc.run_start_ms,
            end_ms=now_ms,
        )
        self._final_written = True

    def force_flush(self) -> None:
        self._writer.flush()

    def dashboard_snapshot(self, *, now_ms: float | None = None) -> dict[str, Any]:
        now = now_ms if now_ms is not None else time.time() * 1000.0
        snap = self.acc.snapshot(now_ms=now, fingerprint=self.fingerprint)
        decision = decide(snap)
        return {
            "label": "SHADOW_PAPER_VALIDATION",
            "STATUS": decision["SHADOW_VALIDATION_VERDICT"],
            "STRATEGY": STRATEGY_DISPLAY_NAME,
            "STRATEGY_ID": STRATEGY_ID,
            "Frozen": "YES",
            "Production": "DISABLED",
            "execution_enabled": False,
            "alters_execution": False,
            "paper_executor_live_trading": False,
            "strategy_fingerprint": self.fingerprint,
            "run_fingerprint": self.run_fingerprint,
            "git_commit": self.identity.get("git_commit"),
            "historical": HISTORICAL_FINAL_VALIDATION,
            "pending": len(self._pending),
            "cycles": self._cycles,
            **snap,
            **decision,
        }

    @property
    def pending_count(self) -> int:
        return len(self._pending)
