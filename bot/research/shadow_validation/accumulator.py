"""Bounded rolling metrics. No unbounded lists of books or waterfalls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.shadow_validation.economics import accounting_pass
from bot.research.shadow_validation.funnel import ExecutionFunnel
from bot.research.shadow_validation.outcomes import DATA_INVALID, ObservationResult
from bot.research.shadow_validation.protocol import (
    ADVERSE_BPS,
    MAX_GAP_SAMPLES,
    MAX_TOP_WINDOW_SHARE,
    MIN_CALENDAR_DAYS,
    MIN_COMPLETE_WINDOWS,
    MIN_VALID_OBSERVATIONS,
    PREFERRED_CALENDAR_DAYS,
    PREFERRED_COMPLETE_WINDOWS,
    WINDOW_SECONDS_LIVE,
)
from bot.research.tournament.criteria import MAX_TOP_SYMBOL_PNL_SHARE

_PERCENTILES = (10, 25, 50, 75, 90, 95, 99)


def _percentile(sorted_xs: list[float], p: float) -> float | None:
    n = len(sorted_xs)
    if n == 0:
        return None
    if n == 1:
        return sorted_xs[0]
    idx = int(round((p / 100.0) * (n - 1)))
    idx = min(n - 1, max(0, idx))
    return sorted_xs[idx]


def dist_of(
    xs: list[float], *, total_n: int | None = None, total_sum: float | None = None
) -> dict[str, float | None]:
    s = sorted(xs)
    n = total_n if total_n is not None else len(s)
    sm = total_sum if total_sum is not None else (sum(s) if s else 0.0)
    mean = (sm / n) if n else None
    out: dict[str, float | None] = {
        "mean": mean,
        "median": _percentile(s, 50) if s else None,
        "n": float(n),
        "sample_n": float(len(s)),
    }
    for p in _PERCENTILES:
        out[f"p{p}"] = _percentile(s, p) if s else None
    return out


@dataclass
class ShadowAccumulator:
    n_candidates: int = 0
    n_completed: int = 0
    n_valid: int = 0
    n_invalid: int = 0
    n_full: int = 0
    n_partial: int = 0
    n_no_fill: int = 0
    n_stale: int = 0
    n_quote_disappeared: int = 0
    n_follower_unavailable: int = 0
    n_hedge_worsened: int = 0
    n_skipped_pending_full: int = 0
    n_accounting_fail: int = 0
    n_quote_survived: int = 0
    n_follower_available: int = 0
    n_identity_fail: int = 0
    sum_expected_net: float = 0.0
    sum_shadow_net: float = 0.0
    sum_realized_net: float = 0.0
    n_realized: int = 0
    sum_gap: float = 0.0
    sum_market_gap: float = 0.0
    sum_total_gap: float = 0.0
    sum_hedge_det: float = 0.0
    n_hedge_det: int = 0
    sum_adverse: float = 0.0
    n_adverse: int = 0
    run_start_ms: float | None = None
    window_net: dict[int, float] = field(default_factory=dict)
    window_count: dict[int, int] = field(default_factory=dict)
    symbol_net: dict[str, float] = field(default_factory=dict)
    hour_net: dict[int, float] = field(default_factory=dict)
    funnel: ExecutionFunnel = field(default_factory=ExecutionFunnel)
    _pred_all: list[float] = field(default_factory=list)
    _pred_full: list[float] = field(default_factory=list)
    _pred_partial: list[float] = field(default_factory=list)
    _mkt_all: list[float] = field(default_factory=list)
    _tot_all: list[float] = field(default_factory=list)
    _pred_by_symbol: dict[str, list[float]] = field(default_factory=dict)
    _pred_by_window: dict[int, list[float]] = field(default_factory=dict)
    _markout: dict[str, list[float]] = field(default_factory=dict)

    def observe_signal(self) -> None:
        self.n_candidates += 1
        self.funnel.observe_signal()

    def skip_pending_full(self) -> None:
        self.n_skipped_pending_full += 1

    def complete(
        self, result: ObservationResult, *, expected: Any, shadow: dict[str, float] | None = None
    ) -> None:
        self.n_completed += 1
        if result.outcome == DATA_INVALID:
            self.n_invalid += 1
        else:
            self.n_valid += 1
        if result.outcome == "FULL_FILL":
            self.n_full += 1
        elif result.outcome == "PARTIAL_FILL":
            self.n_partial += 1
        elif result.outcome == "NO_FILL":
            self.n_no_fill += 1
        elif result.outcome == "STALE":
            self.n_stale += 1
        elif result.outcome == "QUOTE_DISAPPEARED":
            self.n_quote_disappeared += 1
        elif result.outcome == "FOLLOWER_UNAVAILABLE":
            self.n_follower_unavailable += 1
        elif result.outcome == "HEDGE_WORSENED":
            self.n_hedge_worsened += 1

        self.funnel.observe_outcome(outcome=result.outcome, has_5s_markout=result.markout is not None)
        if not result.identities_ok:
            self.n_identity_fail += 1
            self.n_accounting_fail += 1

        if result.outcome != DATA_INVALID:
            self.sum_expected_net += result.expected_net
            self.sum_shadow_net += result.shadow_execution_net
            self.sum_gap += result.prediction_gap
            self._push(self._pred_all, result.prediction_gap)
            if result.outcome == "FULL_FILL":
                self._push(self._pred_full, result.prediction_gap)
            elif result.outcome == "PARTIAL_FILL":
                self._push(self._pred_partial, result.prediction_gap)
            if result.market_gap is not None:
                self.sum_market_gap += result.market_gap
                self._push(self._mkt_all, result.market_gap)
            if result.total_gap is not None:
                self.sum_total_gap += result.total_gap
                self._push(self._tot_all, result.total_gap)
            if result.realized_market_net is not None:
                self.sum_realized_net += result.realized_market_net
                self.n_realized += 1
            if result.quote_survival:
                self.n_quote_survived += 1
            if result.follower_availability:
                self.n_follower_available += 1
            if result.hedge_deterioration_bps is not None:
                self.sum_hedge_det += result.hedge_deterioration_bps
                self.n_hedge_det += 1
            if result.adverse_selection_bps is not None:
                self.sum_adverse += result.adverse_selection_bps
                self.n_adverse += 1
            for hz, bps in (result.markouts_bps or {}).items():
                if bps is None:
                    continue
                self._push(self._markout.setdefault(hz, []), bps)
            sym = result.symbol or "UNKNOWN"
            self.symbol_net[sym] = self.symbol_net.get(sym, 0.0) + result.shadow_execution_net
            self._push(self._pred_by_symbol.setdefault(sym, []), result.prediction_gap)
            if self.run_start_ms is not None:
                sig_ms = float(result.record.get("signal_time_ms") or 0.0)
                w = int(max(0.0, (sig_ms - self.run_start_ms) / 1000.0) // WINDOW_SECONDS_LIVE)
                self.window_net[w] = self.window_net.get(w, 0.0) + result.shadow_execution_net
                self.window_count[w] = self.window_count.get(w, 0) + 1
                self._push(self._pred_by_window.setdefault(w, []), result.prediction_gap)
                hour = int(sig_ms // 3_600_000.0)
                self.hour_net[hour] = self.hour_net.get(hour, 0.0) + result.shadow_execution_net

        if expected is not None and result.fill_fraction > 0.0 and expected.residual() > 1e-8:
            self.n_accounting_fail += 1
        elif expected is not None and result.fill_fraction == 0.0:
            sh = shadow or {
                "shadow_gross": 0.0,
                "shadow_fees": 0.0,
                "shadow_slippage": 0.0,
                "shadow_adverse": 0.0,
                "shadow_latency": 0.0,
                "shadow_execution_net": result.shadow_execution_net,
            }
            if not result.identities_ok:
                return
            if not accounting_pass(expected, sh):
                self.n_accounting_fail += 1

    def complete_from_record(self, record: dict[str, Any]) -> None:
        """Replay a compact JSONL record. Used by the disk reducer only."""
        from bot.research.shadow_validation.outcomes import ObservationResult

        b = record.get("B_EXPECTED_ECONOMICS") or {}
        c = record.get("C_SHADOW_EXECUTION") or {}
        d = record.get("D_REALIZED_MARKET_OUTCOME") or {}
        result = ObservationResult(
            candidate_id=str(record.get("candidate_id") or ""),
            strategy_fingerprint=str(record.get("strategy_fingerprint") or ""),
            outcome=str(record.get("outcome") or DATA_INVALID),
            fill_fraction=float(c.get("fill_fraction") or 0.0),
            shadow_fill=bool(c.get("shadow_fill")),
            shadow_partial_fill=bool(c.get("shadow_partial_fill")),
            shadow_fill_price=c.get("shadow_fill_price"),
            shadow_hedge_price=c.get("shadow_hedge_price"),
            shadow_execution_net=float(c.get("shadow_execution_net") or 0.0),
            expected_net=float(b.get("expected_net") or 0.0),
            execution_gap=float(record.get("execution_gap") or record.get("prediction_gap") or 0.0),
            realized_market_net=d.get("realized_market_net"),
            prediction_gap=float(record.get("prediction_gap") or record.get("execution_gap") or 0.0),
            market_gap=record.get("market_gap"),
            total_gap=record.get("total_gap"),
            identities_ok=bool(record.get("accounting_identities_ok", True)),
            quote_survival=bool(d.get("quote_survival")),
            follower_availability=bool(d.get("follower_availability")),
            hedge_deterioration_bps=d.get("hedge_deterioration_bps"),
            adverse_selection_bps=d.get("adverse_selection_bps"),
            markout=d.get("markout"),
            markouts_bps=dict(d.get("markouts_bps") or {}),
            future_mid=d.get("future_mid"),
            future_bid=d.get("future_bid"),
            future_ask=d.get("future_ask"),
            book_survival=bool(d.get("book_survival")),
            traded_through=bool(d.get("traded_through")),
            duration_until_invalidation_ms=None,
            symbol=str(record.get("symbol") or ""),
            record=record,
        )
        n_cand = self.n_candidates
        self.observe_signal()
        self.n_candidates = n_cand + 1
        self.complete(result, expected=None)

    def update_late_markout(self, *, horizon: str, signed_fraction: float) -> None:
        self._push(self._markout.setdefault(horizon, []), signed_fraction * 10000.0)

    def _push(self, xs: list[float], value: float) -> None:
        if len(xs) < MAX_GAP_SAMPLES:
            xs.append(float(value))

    def complete_windows(self, now_ms: float) -> int:
        if self.run_start_ms is None:
            return 0
        elapsed_s = max(0.0, (now_ms - self.run_start_ms) / 1000.0)
        return int(elapsed_s // WINDOW_SECONDS_LIVE)

    def calendar_days(self, now_ms: float) -> float:
        if self.run_start_ms is None:
            return 0.0
        return max(0.0, (now_ms - self.run_start_ms) / 1000.0 / 86400.0)

    def sample_horizon_met(self, now_ms: float) -> bool:
        return (
            self.complete_windows(now_ms) >= MIN_COMPLETE_WINDOWS
            and self.calendar_days(now_ms) >= float(MIN_CALENDAR_DAYS)
        )

    def sample_volume_met(self) -> bool:
        return self.n_valid >= MIN_VALID_OBSERVATIONS

    def sample_complete(self, now_ms: float) -> bool:
        return self.sample_horizon_met(now_ms) and self.sample_volume_met()

    def preferred_complete(self, now_ms: float) -> bool:
        return (
            self.complete_windows(now_ms) >= PREFERRED_COMPLETE_WINDOWS
            and self.calendar_days(now_ms) >= float(PREFERRED_CALENDAR_DAYS)
            and self.sample_volume_met()
        )

    def gap_distribution(self) -> dict[str, float | None]:
        mean = (self.sum_gap / self.n_valid) if self.n_valid else None
        d = dist_of(self._pred_all, total_n=self.n_valid, total_sum=self.sum_gap)
        d["mean"] = mean
        return d

    def rates(self) -> dict[str, float]:
        v = float(self.n_valid) if self.n_valid else 0.0
        t = float(self.n_completed) if self.n_completed else 0.0

        def _r(n: int, den: float) -> float:
            return (n / den) if den else 0.0

        return {
            "fill_rate": _r(self.n_full, v),
            "partial_fill_rate": _r(self.n_partial, v),
            "no_fill_rate": _r(self.n_no_fill, v),
            "quote_survival_rate": _r(self.n_quote_survived, v),
            "follower_availability_rate": _r(self.n_follower_available, v),
            "hedge_failure_rate": _r(self.n_follower_unavailable + self.n_hedge_worsened, v),
            "data_invalid_rate": _r(self.n_invalid, t),
            "mean_hedge_deterioration_bps": (self.sum_hedge_det / self.n_hedge_det) if self.n_hedge_det else 0.0,
            "mean_adverse_selection_bps": (self.sum_adverse / self.n_adverse) if self.n_adverse else 0.0,
        }

    def _share(self, mapping: dict[Any, float]) -> float:
        if not mapping:
            return 0.0
        total = sum(mapping.values())
        if total > 0.0:
            return max(mapping.values()) / total
        denom = sum(abs(v) for v in mapping.values())
        return (max(abs(v) for v in mapping.values()) / denom) if denom else 0.0

    def top_window_share(self) -> float:
        return self._share(self.window_net)

    def top_symbol_share(self) -> float:
        return self._share(self.symbol_net)

    def top_hour_share(self) -> float:
        return self._share(self.hour_net)

    def window_stability(self, now_ms: float) -> dict[str, Any]:
        closed = self.complete_windows(now_ms)
        pos = neg = 0
        for w, net in self.window_net.items():
            if w >= closed:
                continue
            if net > 0:
                pos += 1
            elif net < 0:
                neg += 1
        return {
            "positive_windows": pos,
            "negative_windows": neg,
            "closed_windows_with_signals": pos + neg,
            "top_symbol_share": self.top_symbol_share(),
            "top_window_share": self.top_window_share(),
            "top_hour_share": self.top_hour_share(),
            "window_dominance_cap": MAX_TOP_WINDOW_SHARE,
            "symbol_share_cap": MAX_TOP_SYMBOL_PNL_SHARE,
            "ROUTE_UNIVERSE_LIMITED": True,
            "route": "okx|bitvavo",
        }

    def execution_gap_block(self) -> dict[str, Any]:
        return {
            "all_candidates": dist_of(self._pred_all, total_n=self.n_valid, total_sum=self.sum_gap),
            "full_fills": dist_of(self._pred_full),
            "partial_fills": dist_of(self._pred_partial),
            "by_symbol": {k: dist_of(v) for k, v in sorted(self._pred_by_symbol.items())},
            "by_window": {str(k): dist_of(v) for k, v in sorted(self._pred_by_window.items())},
            "note": "prediction_gap = shadow_execution_net - expected_net",
        }

    def market_gap_block(self) -> dict[str, Any]:
        return {
            "all_candidates": dist_of(self._mkt_all, total_n=self.n_realized, total_sum=self.sum_market_gap),
            "total_gap": dist_of(self._tot_all, total_n=self.n_realized, total_sum=self.sum_total_gap),
            "note": "market_gap = realized_market_net - shadow_execution_net",
        }

    def adverse_block(self) -> dict[str, Any]:
        assumed = float(ADVERSE_BPS)
        out: dict[str, Any] = {
            "research_adverse_assumption_bps": assumed,
            "descriptive_only": True,
            "rejection_threshold_defined": False,
        }
        for hz in ("1s", "5s", "30s", "60s"):
            xs = self._markout.get(hz) or []
            signed = dist_of(xs)
            adv = dist_of([max(0.0, -x) for x in xs])
            out[f"realized_{hz}_markout"] = signed
            out[f"observed_adverse_{hz}"] = adv
            if adv.get("mean") is not None:
                out[f"adverse_gap_{hz}_bps"] = float(adv["mean"]) - assumed
        adv5 = out.get("observed_adverse_5s") or {}
        if adv5.get("mean") is not None:
            out["adverse_gap"] = float(adv5["mean"]) - assumed
        return out

    def snapshot(self, *, now_ms: float, fingerprint: str) -> dict[str, Any]:
        rates = self.rates()
        return {
            "strategy_fingerprint": fingerprint,
            "n_candidates": self.n_candidates,
            "n_completed": self.n_completed,
            "valid_observations": self.n_valid,
            "invalid_observations": self.n_invalid,
            "FULL_FILL": self.n_full,
            "PARTIAL_FILL": self.n_partial,
            "NO_FILL": self.n_no_fill,
            "STALE": self.n_stale,
            "QUOTE_DISAPPEARED": self.n_quote_disappeared,
            "FOLLOWER_UNAVAILABLE": self.n_follower_unavailable,
            "HEDGE_WORSENED": self.n_hedge_worsened,
            "DATA_INVALID": self.n_invalid,
            "n_skipped_pending_full": self.n_skipped_pending_full,
            "RESEARCH_EXPECTED_NET": self.sum_expected_net,
            "LIVE_SHADOW_EXECUTION_NET": self.sum_shadow_net,
            "REALIZED_MARKET_NET": self.sum_realized_net,
            "execution_gap_sum": self.sum_gap,
            "execution_gap": self.gap_distribution(),
            "prediction_gap": self.execution_gap_block(),
            "market_gap": self.market_gap_block(),
            "funnel": self.funnel.snapshot(),
            "adverse": self.adverse_block(),
            "rates": rates,
            "complete_windows": self.complete_windows(now_ms),
            "calendar_days": self.calendar_days(now_ms),
            "min_windows": MIN_COMPLETE_WINDOWS,
            "min_calendar_days": MIN_CALENDAR_DAYS,
            "min_valid_observations": MIN_VALID_OBSERVATIONS,
            "preferred_windows": PREFERRED_COMPLETE_WINDOWS,
            "preferred_calendar_days": PREFERRED_CALENDAR_DAYS,
            "sample_horizon_met": self.sample_horizon_met(now_ms),
            "sample_volume_met": self.sample_volume_met(),
            "sample_complete": self.sample_complete(now_ms),
            "preferred_complete": self.preferred_complete(now_ms),
            "top_window_share": self.top_window_share(),
            "top_symbol_share": self.top_symbol_share(),
            "top_hour_share": self.top_hour_share(),
            "stability": self.window_stability(now_ms),
            "accounting_fail": self.n_accounting_fail,
            "identity_fail": self.n_identity_fail,
            "pending_bound": MAX_GAP_SAMPLES,
            "gap_samples": len(self._pred_all),
            "ROUTE_UNIVERSE_LIMITED": True,
        }

    def window_summary(self, window_id: int) -> dict[str, Any]:
        return {
            "window_id": window_id,
            "LIVE_SHADOW_EXECUTION_NET": self.window_net.get(window_id, 0.0),
            "n": self.window_count.get(window_id, 0),
            "prediction_gap": dist_of(self._pred_by_window.get(window_id) or []),
        }

    def bounded_memory(self) -> bool:
        if len(self._pred_all) > MAX_GAP_SAMPLES:
            return False
        if len(self.window_net) >= 10_000:
            return False
        extra = sum(len(v) for v in self._pred_by_symbol.values())
        extra += sum(len(v) for v in self._markout.values())
        return extra < MAX_GAP_SAMPLES * 8
