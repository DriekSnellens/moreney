"""Live shadow observer. Simulated execution only. No exchange orders."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.research.execution_realism.config import EXECUTION_REALISM_PRODUCTION_ENABLED
from bot.research.shadow_validation.accumulator import ShadowAccumulator
from bot.research.shadow_validation.artifacts import (
    CompactObservationWriter,
    ResumeIncompatibleError,
    atomic_write_json,
    new_run_id,
    run_dir_for,
    run_paths,
    write_accumulator_snapshot,
    write_manifest,
)
from bot.research.shadow_validation.books import CompactL1, L1View, inspect_l1
from bot.research.shadow_validation.detector import detect_signal
from bot.research.shadow_validation.economics import ExpectedEconomics, expected_from_dislocation
from bot.research.shadow_validation.identity import (
    ensure_frozen_identity,
    event_identity,
)
from bot.research.shadow_validation.outcomes import classify_observation
from bot.research.shadow_validation.protocol import (
    ARTIFACT_SCHEMA_VERSION,
    ENTRY_OBSERVE_MS,
    HEDGE_OBSERVE_MS,
    HISTORICAL_FINAL_VALIDATION,
    HORIZON_MS,
    MAX_PENDING,
    PRODUCTION_EXECUTION_ENABLED,
    RUNTIME_ID_LIVE,
    STRATEGY_DISPLAY_NAME,
    STRATEGY_ID,
    VENUE_A,
    VENUE_B,
    acceptance_hash,
)
from bot.research.shadow_validation.report import maybe_write_final
from bot.research.shadow_validation.scorecard import build_scorecard, progress_sentence
from bot.research.shadow_validation.verdict import decide

logger = logging.getLogger(__name__)

_MAX_FOLLOWUPS = 256


def _signed_markout(decision: CompactL1, later: CompactL1 | None, *, a_rich: bool) -> float | None:
    if later is None or decision.mid <= 0.0 or later.mid <= 0.0:
        return None
    raw = (later.mid - decision.mid) / decision.mid
    return -raw if a_rich else raw


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
    markout_1s_due_ms: float
    decision_entry: CompactL1
    decision_hedge: CompactL1
    expected: ExpectedEconomics
    later_entry: L1View | None = None
    later_hedge: L1View | None = None
    markout_1s: float | None = None


@dataclass(slots=True)
class _Followup:
    candidate_id: str
    symbol: str
    a_rich: bool
    decision_entry: CompactL1
    due_30_ms: float
    due_60_ms: float
    got_30: bool = False
    got_60: bool = False


class ShadowPaperObserver:
    """Observe frozen CVD on the live book path. Never places orders."""

    alters_execution = False
    execution_enabled = False

    def __init__(
        self,
        *,
        run_dir: str | None = None,
        run_id: str | None = None,
        resume: bool = False,
        git_commit_override: str | None = None,
        now_ms: float | None = None,
        runtime_id: str | None = None,
    ) -> None:
        if PRODUCTION_EXECUTION_ENABLED or EXECUTION_REALISM_PRODUCTION_ENABLED:
            raise RuntimeError("shadow observer refuses to start with production execution enabled")
        self.run_id = run_id or new_run_id()
        self.run_dir = str(run_dir or run_dir_for(self.run_id))
        self.resume = resume
        self.runtime_id = runtime_id or RUNTIME_ID_LIVE
        identity, invalidated, integrity = ensure_frozen_identity(
            run_dir=self.run_dir,
            git_commit_override=git_commit_override,
            validation_run_id=self.run_id,
            runtime_id=self.runtime_id,
            resume=resume,
        )
        if identity.get("validation_run_id"):
            self.run_id = str(identity["validation_run_id"])
        self.identity = identity
        self.fingerprint = str(identity["strategy_fingerprint"])
        self.run_fingerprint = str(identity["run_fingerprint"])
        self.invalidated_prior = invalidated
        self.integrity = integrity
        self.acc = ShadowAccumulator()
        now = now_ms if now_ms is not None else time.time() * 1000.0
        paths = run_paths(Path(self.run_dir))
        self._paths = paths
        if resume:
            from bot.research.shadow_validation.reducer import reduce_run

            reduced = reduce_run(paths["run_dir"], identity=identity)
            self.acc = reduced["accumulator"]
            self.integrity = reduced["VALIDATION_INTEGRITY"]
            if self.integrity == "MIXED_DATA":
                raise ResumeIncompatibleError("mixed strategy fingerprints in observations")
        else:
            self.acc.run_start_ms = now
        if self.acc.run_start_ms is None:
            self.acc.run_start_ms = now
        self._pending: dict[str, _Pending] = {}
        self._followups: dict[str, _Followup] = {}
        self._inflight_symbols: set[str] = set()
        self._seq = 0
        self._final_written = paths["final_results"].exists()
        self._writer = CompactObservationWriter(paths["observations"])
        self._acc_path = paths["accumulator"]
        self._cycles = 0
        self._last_window_written = -1
        write_manifest(
            paths["manifest"],
            {
                "validation_run_id": self.run_id,
                "strategy_fingerprint": self.fingerprint,
                "config_hash": identity.get("config_hash"),
                "acceptance_hash": identity.get("acceptance_hash") or acceptance_hash(),
                "protocol_hash": identity.get("protocol_hash"),
                "git_commit": identity.get("git_commit"),
                "runtime_id": identity.get("runtime_id"),
                "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                "production_execution": "DISABLED",
                "run_start_ms": self.acc.run_start_ms,
            },
        )

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
        self._resolve_followups(books, now)
        self._detect_new(books, symbols, now)
        self._maybe_write_window(now)
        if self._cycles % 32 == 0:
            self._write_summaries(now)
        self._maybe_finalize(now)

    def _resolve_due(self, books: dict[str, dict[str, Any]], now_ms: float) -> None:
        done: list[str] = []
        ident = event_identity(self.identity)
        for cid, pend in self._pending.items():
            if pend.later_entry is None and now_ms >= pend.entry_due_ms:
                pend.later_entry = self._view(books, pend.decision_entry.venue, pend.symbol, now_ms)
            if pend.later_hedge is None and now_ms >= pend.hedge_due_ms:
                pend.later_hedge = self._view(books, pend.decision_hedge.venue, pend.symbol, now_ms)
            if pend.markout_1s is None and now_ms >= pend.markout_1s_due_ms:
                v = self._view(books, pend.decision_entry.venue, pend.symbol, now_ms)
                pend.markout_1s = _signed_markout(pend.decision_entry, v.l1, a_rich=pend.a_rich)
            if now_ms < pend.horizon_due_ms:
                continue
            future = self._view(books, pend.decision_entry.venue, pend.symbol, now_ms)
            later_entry = pend.later_entry or future
            later_hedge = pend.later_hedge or self._view(
                books, pend.decision_hedge.venue, pend.symbol, now_ms
            )
            m5 = _signed_markout(pend.decision_entry, future.l1, a_rich=pend.a_rich)
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
                identity=ident,
                symbol=pend.symbol,
                markouts_fraction={"1s": pend.markout_1s, "5s": m5},
            )
            self.acc.complete(result, expected=pend.expected)
            self._writer.enqueue(result.record)
            if len(self._followups) < _MAX_FOLLOWUPS:
                self._followups[cid] = _Followup(
                    candidate_id=cid,
                    symbol=pend.symbol,
                    a_rich=pend.a_rich,
                    decision_entry=pend.decision_entry,
                    due_30_ms=pend.decision_ts_ms + 30_000.0,
                    due_60_ms=pend.decision_ts_ms + 60_000.0,
                )
            done.append(cid)
        for cid in done:
            pend = self._pending.pop(cid)
            self._inflight_symbols.discard(pend.symbol)

    def _resolve_followups(self, books: dict[str, dict[str, Any]], now_ms: float) -> None:
        done: list[str] = []
        for cid, fu in self._followups.items():
            view = self._view(books, fu.decision_entry.venue, fu.symbol, now_ms)
            if not fu.got_30 and now_ms >= fu.due_30_ms:
                signed = _signed_markout(fu.decision_entry, view.l1, a_rich=fu.a_rich)
                if signed is not None:
                    self.acc.update_late_markout(horizon="30s", signed_fraction=signed)
                fu.got_30 = True
            if not fu.got_60 and now_ms >= fu.due_60_ms:
                signed = _signed_markout(fu.decision_entry, view.l1, a_rich=fu.a_rich)
                if signed is not None:
                    self.acc.update_late_markout(horizon="60s", signed_fraction=signed)
                fu.got_60 = True
                done.append(cid)
        for cid in done:
            self._followups.pop(cid, None)

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
                markout_1s_due_ms=now_ms + 1000.0,
                decision_entry=sig.entry,
                decision_hedge=sig.hedge,
                expected=expected,
            )
            self._inflight_symbols.add(symbol)

    def _maybe_write_window(self, now_ms: float) -> None:
        closed = self.acc.complete_windows(now_ms) - 1
        if closed < 0 or closed <= self._last_window_written:
            return
        for w in range(self._last_window_written + 1, closed + 1):
            atomic_write_json(
                self._paths["windows"] / f"W{w:04d}.json",
                self.acc.window_summary(w),
            )
        self._last_window_written = closed

    def _write_summaries(self, now_ms: float) -> None:
        snap = self.acc.snapshot(now_ms=now_ms, fingerprint=self.fingerprint)
        write_accumulator_snapshot(self._acc_path, snap)
        atomic_write_json(self._paths["execution_gap"], snap.get("prediction_gap") or {})
        atomic_write_json(self._paths["adverse"], snap.get("adverse") or {})
        atomic_write_json(self._paths["funnel"], snap.get("funnel") or {})
        if self.acc.run_start_ms is not None:
            day = int((now_ms - self.acc.run_start_ms) / 86400000.0)
            atomic_write_json(
                self._paths["daily"] / f"D{day:04d}.json",
                {
                    "day_index": day,
                    "calendar_days": self.acc.calendar_days(now_ms),
                    "n_candidates": self.acc.n_candidates,
                    "valid": self.acc.n_valid,
                    "LIVE_SHADOW_EXECUTION_NET": self.acc.sum_shadow_net,
                },
            )

    def _maybe_finalize(self, now_ms: float) -> None:
        if not self.acc.sample_complete(now_ms):
            return
        self._writer.flush()
        self._write_summaries(now_ms)
        snap = self.acc.snapshot(now_ms=now_ms, fingerprint=self.fingerprint)
        decision = decide(snap)
        if not self._final_written:
            maybe_write_final(
                identity=self.identity,
                snapshot=snap,
                decision=decision,
                run_start_ms=self.acc.run_start_ms or 0.0,
                end_ms=now_ms,
                run_dir=self.run_dir,
            )
            self._final_written = True
        # Passive collection continues toward the preferred 50/14 target.

    def force_flush(self) -> None:
        self._writer.flush()

    def dashboard_snapshot(self, *, now_ms: float | None = None) -> dict[str, Any]:
        now = now_ms if now_ms is not None else time.time() * 1000.0
        snap = self.acc.snapshot(now_ms=now, fingerprint=self.fingerprint)
        decision = decide(snap)
        card = build_scorecard(snap, decision, integrity=self.integrity, identity=self.identity)
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
            "validation_run_id": self.run_id,
            "git_commit": self.identity.get("git_commit"),
            "VALIDATION_INTEGRITY": self.integrity,
            "historical": HISTORICAL_FINAL_VALIDATION,
            "pending": len(self._pending),
            "followups": len(self._followups),
            "cycles": self._cycles,
            "scorecard": card,
            "progress_sentence": progress_sentence(snap),
            **snap,
            **decision,
        }

    @property
    def pending_count(self) -> int:
        return len(self._pending)
