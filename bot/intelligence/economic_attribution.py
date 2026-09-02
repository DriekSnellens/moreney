"""Economic attribution store — analytics layer, NOT authoritative PnL.

Exchange FIFO and portfolio PnL remain the source of truth for realized PnL.
This module tracks opportunity/execution attribution, counterfactuals, and calibration.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HOUR = Decimal("3600")

SHADOW_THRESHOLDS: tuple[Decimal, ...] = tuple(
    Decimal(str(x)) for x in ("0.60", "0.65", "0.70", "0.75", "0.80", "0.85", "0.90")
)

ADVERSE_BUCKETS: tuple[tuple[Decimal, Decimal], ...] = tuple(
    (Decimal(str(i)) / 10, Decimal(str(i + 1)) / 10) for i in range(10)
)

FILL_PROB_BUCKETS: tuple[tuple[Decimal, Decimal], ...] = (
    (_ZERO, Decimal("0.2")),
    (Decimal("0.2"), Decimal("0.4")),
    (Decimal("0.4"), Decimal("0.6")),
    (Decimal("0.6"), Decimal("0.8")),
    (Decimal("0.8"), _ONE),
)

SCORE_BUCKETS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("40")),
    (Decimal("40"), Decimal("55")),
    (Decimal("55"), Decimal("70")),
    (Decimal("70"), Decimal("80")),
    (Decimal("80"), Decimal("101")),
)


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _clamp01(v: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, v))


def _bucket_index(value: Decimal, buckets: Sequence[tuple[Decimal, Decimal]]) -> int | None:
    for i, (lo, hi) in enumerate(buckets):
        if value >= lo and (value < hi or (hi == _ONE and value <= hi)):
            return i
    return None


def net_per_capital_hour(realized_net: Decimal, capital: Decimal, hold_seconds: Decimal | None) -> Decimal | None:
    if capital <= 0 or hold_seconds is None or hold_seconds <= 0:
        return None
    hours = hold_seconds / _HOUR
    if hours <= 0:
        return None
    return realized_net / capital / hours


def net_per_hour(realized_net: Decimal, hold_seconds: Decimal | None) -> Decimal | None:
    if hold_seconds is None or hold_seconds <= 0:
        return None
    return realized_net / (hold_seconds / _HOUR)


@dataclass
class AttributionRecord:
    """Single opportunity/execution attribution event."""

    record_id: str
    timestamp: str
    symbol: str
    venue: str
    strategy: str
    side: str
    experiment_id: str = "phase2_intelligence"

    opportunity_score_before: Decimal | None = None
    opportunity_score_after: Decimal | None = None
    regime: str | None = None
    regime_confidence: Decimal | None = None
    regime_score: Decimal | None = None
    adverse_selection_score: Decimal | None = None
    execution_decision: str | None = None

    order_price: Decimal | None = None
    fill_price: Decimal | None = None
    size: Decimal | None = None
    expected_net: Decimal | None = None
    realized_net: Decimal | None = None

    created_at: str | None = None
    fill_at: str | None = None
    exit_at: str | None = None
    hold_seconds: Decimal | None = None

    mfe: Decimal | None = None
    mae: Decimal | None = None
    post_fill_return_1s: Decimal | None = None
    post_fill_return_5s: Decimal | None = None
    post_fill_return_15s: Decimal | None = None
    post_fill_return_30s: Decimal | None = None

    was_cancelled_by_intelligence: bool = False
    was_repriced: bool = False
    cancel_reason: str | None = None

    counterfactual_action: str | None = None
    counterfactual_price: Decimal | None = None
    counterfactual_size: Decimal | None = None
    counterfactual_expected_net: Decimal | None = None
    counterfactual_mfe: Decimal | None = None
    counterfactual_mae: Decimal | None = None
    counterfactual_adverse_move: Decimal | None = None
    counterfactual_net: Decimal | None = None

    avoided_loss_eur: Decimal | None = None
    missed_opportunity_eur: Decimal | None = None
    cancel_alpha_eur: Decimal | None = None

    maker_expected_net: Decimal | None = None
    maker_fill_probability: Decimal | None = None
    maker_expected_wait: Decimal | None = None
    taker_expected_net: Decimal | None = None
    taker_probability: Decimal | None = None
    selected_execution: str | None = None
    execution_alpha: Decimal | None = None

    capital_deployed: Decimal | None = None
    capital_locked: Decimal | None = None
    net_per_capital_hour: Decimal | None = None
    net_per_hour: Decimal | None = None

    predicted_fill_probability: Decimal | None = None
    actual_fill: bool = False
    toxic_fill: bool = False

    underwater_amount: Decimal | None = None
    underwater_duration_sec: Decimal | None = None

    decision_reasons: tuple[str, ...] = ()


@dataclass
class BucketStats:
    samples: int = 0
    fills: int = 0
    toxic_fills: int = 0
    sum_adverse_move: Decimal = _ZERO
    sum_net: Decimal = _ZERO
    sum_mfe: Decimal = _ZERO
    sum_mae: Decimal = _ZERO
    sum_predicted_prob: Decimal = _ZERO
    sum_actual_fill: Decimal = _ZERO

    def record(
        self,
        *,
        filled: bool = False,
        toxic: bool = False,
        adverse_move: Decimal | None = None,
        net: Decimal | None = None,
        mfe: Decimal | None = None,
        mae: Decimal | None = None,
        predicted_prob: Decimal | None = None,
    ) -> None:
        self.samples += 1
        if filled:
            self.fills += 1
            self.sum_actual_fill += _ONE
        if toxic:
            self.toxic_fills += 1
        if adverse_move is not None:
            self.sum_adverse_move += adverse_move
        if net is not None:
            self.sum_net += net
        if mfe is not None:
            self.sum_mfe += mfe
        if mae is not None:
            self.sum_mae += mae
        if predicted_prob is not None:
            self.sum_predicted_prob += predicted_prob

    def snapshot(self, label: str) -> dict[str, Any]:
        n = self.samples or 0
        f = self.fills or 0
        return {
            "bucket": label,
            "samples": n,
            "fills": f,
            "toxic_fills": self.toxic_fills,
            "toxic_fill_rate": str((Decimal(self.toxic_fills) / Decimal(f)).quantize(Decimal("0.001")))
            if f
            else None,
            "average_adverse_move": str((self.sum_adverse_move / f).quantize(Decimal("0.0001")))
            if f
            else None,
            "average_net": str((self.sum_net / f).quantize(Decimal("0.01"))) if f else None,
            "average_mfe": str((self.sum_mfe / f).quantize(Decimal("0.0001"))) if f else None,
            "average_mae": str((self.sum_mae / f).quantize(Decimal("0.0001"))) if f else None,
            "predicted_fill_rate": str((self.sum_predicted_prob / n).quantize(Decimal("0.001")))
            if n
            else None,
            "actual_fill_rate": str((self.sum_actual_fill / n).quantize(Decimal("0.001")))
            if n
            else None,
            "calibration_error": str(
                abs(self.sum_predicted_prob / n - self.sum_actual_fill / n).quantize(Decimal("0.001"))
            )
            if n
            else None,
        }


@dataclass
class GroupStats:
    key: str
    trades: int = 0
    sum_realized_net: Decimal = _ZERO
    sum_hold_seconds: Decimal = _ZERO
    sum_capital: Decimal = _ZERO
    sum_mfe_capture: Decimal = _ZERO
    mfe_samples: int = 0
    toxic_fills: int = 0
    fills: int = 0
    sum_lock_seconds: Decimal = _ZERO
    lock_samples: int = 0
    sum_execution_alpha: Decimal = _ZERO
    exec_alpha_samples: int = 0

    def record(
        self,
        *,
        realized_net: Decimal | None = None,
        hold_seconds: Decimal | None = None,
        capital: Decimal | None = None,
        mfe_capture: Decimal | None = None,
        toxic: bool = False,
        filled: bool = False,
        lock_seconds: Decimal | None = None,
        execution_alpha: Decimal | None = None,
    ) -> None:
        self.trades += 1
        if realized_net is not None:
            self.sum_realized_net += realized_net
        if hold_seconds is not None and hold_seconds > 0:
            self.sum_hold_seconds += hold_seconds
        if capital is not None and capital > 0:
            self.sum_capital += capital
        if mfe_capture is not None:
            self.mfe_samples += 1
            self.sum_mfe_capture += mfe_capture
        if toxic:
            self.toxic_fills += 1
        if filled:
            self.fills += 1
        if lock_seconds is not None:
            self.lock_samples += 1
            self.sum_lock_seconds += lock_seconds
        if execution_alpha is not None:
            self.exec_alpha_samples += 1
            self.sum_execution_alpha += execution_alpha

    def snapshot(self) -> dict[str, Any]:
        n = self.trades or 0
        f = self.fills or 1
        hold = self.sum_hold_seconds
        cap = self.sum_capital
        net = self.sum_realized_net
        nph = net_per_hour(net, hold) if hold > 0 else None
        npch = net_per_capital_hour(net, cap, hold) if cap > 0 and hold > 0 else None
        return {
            "key": self.key,
            "trades": n,
            "realized_net_eur": str(net.quantize(Decimal("0.01"))),
            "net_per_hour": str(nph.quantize(Decimal("0.0001"))) if nph is not None else None,
            "net_per_capital_hour": str(npch.quantize(Decimal("0.0001"))) if npch is not None else None,
            "fill_rate": str((Decimal(self.fills) / Decimal(n)).quantize(Decimal("0.001"))) if n else None,
            "toxic_fill_rate": str((Decimal(self.toxic_fills) / Decimal(f)).quantize(Decimal("0.001")))
            if self.fills
            else None,
            "average_hold_seconds": str((hold / n).quantize(Decimal("0.1"))) if n and hold > 0 else None,
            "average_mfe_capture": str(
                (self.sum_mfe_capture / self.mfe_samples).quantize(Decimal("0.0001"))
            )
            if self.mfe_samples
            else None,
            "average_lock_seconds": str(
                (self.sum_lock_seconds / self.lock_samples).quantize(Decimal("0.1"))
            )
            if self.lock_samples
            else None,
            "average_execution_alpha": str(
                (self.sum_execution_alpha / self.exec_alpha_samples).quantize(Decimal("0.0001"))
            )
            if self.exec_alpha_samples
            else None,
        }


@dataclass
class EconomicAttributionStore:
    """Restart-safe attribution analytics."""

    records: list[AttributionRecord] = field(default_factory=list)
    max_records: int = 5000

    adverse_buckets: list[BucketStats] = field(default_factory=lambda: [BucketStats() for _ in ADVERSE_BUCKETS])
    fill_prob_buckets: list[BucketStats] = field(default_factory=lambda: [BucketStats() for _ in FILL_PROB_BUCKETS])
    score_buckets: list[BucketStats] = field(default_factory=lambda: [BucketStats() for _ in SCORE_BUCKETS])

    symbol_stats: dict[str, GroupStats] = field(default_factory=dict)
    venue_stats: dict[str, GroupStats] = field(default_factory=dict)
    strategy_stats: dict[str, GroupStats] = field(default_factory=dict)
    regime_stats: dict[str, GroupStats] = field(default_factory=dict)

    shadow_threshold_cancels: dict[str, int] = field(default_factory=dict)
    intelligence_cancels: int = 0
    sum_avoided_loss: Decimal = _ZERO
    sum_missed_opportunity: Decimal = _ZERO
    sum_cancel_alpha: Decimal = _ZERO
    cancel_alpha_samples: int = 0

    lock_times: list[float] = field(default_factory=list)
    sum_underwater_capital: Decimal = _ZERO
    underwater_samples: int = 0

    maker_alpha_sum: Decimal = _ZERO
    taker_alpha_sum: Decimal = _ZERO
    maker_samples: int = 0
    taker_samples: int = 0

    def _append(self, rec: AttributionRecord) -> None:
        self.records.append(rec)
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records :]

    def record_opportunity(
        self,
        *,
        record_id: str,
        symbol: str,
        venue: str,
        strategy: str,
        side: str,
        score_before: Decimal | None = None,
        score_after: Decimal | None = None,
        regime: str | None = None,
        regime_confidence: Decimal | None = None,
        regime_score: Decimal | None = None,
        adverse_score: Decimal | None = None,
        execution_decision: str | None = None,
        expected_net: Decimal | None = None,
        order_price: Decimal | None = None,
        size: Decimal | None = None,
        maker_expected_net: Decimal | None = None,
        maker_fill_probability: Decimal | None = None,
        maker_expected_wait: Decimal | None = None,
        taker_expected_net: Decimal | None = None,
        taker_probability: Decimal | None = None,
        selected_execution: str | None = None,
        predicted_fill_probability: Decimal | None = None,
        experiment_id: str = "phase2_intelligence",
        reasons: Sequence[str] = (),
    ) -> AttributionRecord:
        now = datetime.now(timezone.utc).isoformat()
        rec = AttributionRecord(
            record_id=record_id,
            timestamp=now,
            symbol=symbol,
            venue=venue,
            strategy=strategy,
            side=side,
            experiment_id=experiment_id,
            opportunity_score_before=score_before,
            opportunity_score_after=score_after,
            regime=regime,
            regime_confidence=regime_confidence,
            regime_score=regime_score,
            adverse_selection_score=adverse_score,
            execution_decision=execution_decision,
            expected_net=expected_net,
            order_price=order_price,
            size=size,
            maker_expected_net=maker_expected_net,
            maker_fill_probability=maker_fill_probability,
            maker_expected_wait=maker_expected_wait,
            taker_expected_net=taker_expected_net,
            taker_probability=taker_probability,
            selected_execution=selected_execution,
            predicted_fill_probability=predicted_fill_probability,
            created_at=now,
            decision_reasons=tuple(reasons),
        )
        self._append(rec)

        if score_after is not None:
            idx = _bucket_index(score_after, SCORE_BUCKETS)
            if idx is not None:
                self.score_buckets[idx].record()

        if adverse_score is not None:
            idx = _bucket_index(adverse_score, ADVERSE_BUCKETS)
            if idx is not None:
                self.adverse_buckets[idx].record()

        if predicted_fill_probability is not None:
            idx = _bucket_index(_clamp01(predicted_fill_probability), FILL_PROB_BUCKETS)
            if idx is not None:
                self.fill_prob_buckets[idx].record(predicted_prob=predicted_fill_probability)

        if adverse_score is not None:
            for thr in SHADOW_THRESHOLDS:
                key = f"threshold_{int(thr * 100)}_would_cancel"
                if adverse_score >= thr:
                    self.shadow_threshold_cancels[key] = self.shadow_threshold_cancels.get(key, 0) + 1

        return rec

    def record_cancel(
        self,
        rec: AttributionRecord,
        *,
        reason: str,
        avoided_loss: Decimal | None = None,
        missed_opportunity: Decimal | None = None,
        counterfactual_expected_net: Decimal | None = None,
        counterfactual_action: str = "FILL",
        live_executed: bool = True,
    ) -> None:
        rec.was_cancelled_by_intelligence = True
        rec.cancel_reason = reason
        rec.counterfactual_action = counterfactual_action
        rec.counterfactual_expected_net = counterfactual_expected_net
        if avoided_loss is not None:
            rec.avoided_loss_eur = avoided_loss
            self.sum_avoided_loss += avoided_loss
        if missed_opportunity is not None:
            rec.missed_opportunity_eur = missed_opportunity
            self.sum_missed_opportunity += missed_opportunity
        alpha = (avoided_loss or _ZERO) - (missed_opportunity or _ZERO)
        rec.cancel_alpha_eur = alpha
        self.sum_cancel_alpha += alpha
        self.cancel_alpha_samples += 1
        if live_executed:
            self.intelligence_cancels += 1

    def record_fill_outcome(
        self,
        rec: AttributionRecord,
        *,
        fill_price: Decimal,
        realized_net: Decimal | None = None,
        toxic: bool = False,
        adverse_move: Decimal | None = None,
        mfe: Decimal | None = None,
        mae: Decimal | None = None,
        hold_seconds: Decimal | None = None,
        capital_deployed: Decimal | None = None,
        mfe_capture: Decimal | None = None,
        post_fill: dict[str, Decimal] | None = None,
    ) -> None:
        rec.fill_price = fill_price
        rec.actual_fill = True
        rec.fill_at = datetime.now(timezone.utc).isoformat()
        rec.realized_net = realized_net
        rec.toxic_fill = toxic
        rec.hold_seconds = hold_seconds
        rec.mfe = mfe
        rec.mae = mae
        rec.capital_deployed = capital_deployed
        rec.net_per_hour = net_per_hour(realized_net or _ZERO, hold_seconds)
        rec.net_per_capital_hour = net_per_capital_hour(
            realized_net or _ZERO, capital_deployed or _ZERO, hold_seconds
        )
        if post_fill:
            rec.post_fill_return_1s = post_fill.get("1s")
            rec.post_fill_return_5s = post_fill.get("5s")
            rec.post_fill_return_15s = post_fill.get("15s")
            rec.post_fill_return_30s = post_fill.get("30s")

        if rec.adverse_selection_score is not None:
            idx = _bucket_index(rec.adverse_selection_score, ADVERSE_BUCKETS)
            if idx is not None:
                self.adverse_buckets[idx].record(
                    filled=True,
                    toxic=toxic,
                    adverse_move=adverse_move,
                    net=realized_net,
                    mfe=mfe,
                    mae=mae,
                )

        if rec.predicted_fill_probability is not None:
            idx = _bucket_index(_clamp01(rec.predicted_fill_probability), FILL_PROB_BUCKETS)
            if idx is not None:
                self.fill_prob_buckets[idx].record(filled=True, predicted_prob=rec.predicted_fill_probability)

        if rec.opportunity_score_after is not None:
            idx = _bucket_index(rec.opportunity_score_after, SCORE_BUCKETS)
            if idx is not None:
                self.score_buckets[idx].record(net=realized_net, filled=True)

        for store, key in (
            (self.symbol_stats, rec.symbol),
            (self.venue_stats, rec.venue),
            (self.strategy_stats, rec.strategy),
            (self.regime_stats, rec.regime or "UNKNOWN"),
        ):
            if key not in store:
                store[key] = GroupStats(key=key)
            store[key].record(
                realized_net=realized_net,
                hold_seconds=hold_seconds,
                capital=capital_deployed,
                mfe_capture=mfe_capture,
                toxic=toxic,
                filled=True,
                lock_seconds=hold_seconds,
            )

    def record_execution_alpha(
        self,
        rec: AttributionRecord,
        *,
        execution_alpha: Decimal,
        selected: str,
    ) -> None:
        rec.execution_alpha = execution_alpha
        rec.selected_execution = selected
        if selected.upper() == "MAKER":
            self.maker_alpha_sum += execution_alpha
            self.maker_samples += 1
        elif selected.upper() == "TAKER":
            self.taker_alpha_sum += execution_alpha
            self.taker_samples += 1

    def record_underwater(
        self,
        *,
        symbol: str,
        venue: str,
        underwater_amount: Decimal,
        duration_sec: Decimal,
    ) -> None:
        self.sum_underwater_capital += underwater_amount
        self.underwater_samples += 1
        key = f"{symbol}|{venue}"
        if key not in self.symbol_stats:
            self.symbol_stats[key] = GroupStats(key=key)
        self.symbol_stats[key].record(lock_seconds=duration_sec)

    def record_lock_time(self, seconds: float) -> None:
        if seconds > 0:
            self.lock_times.append(seconds)

    def adverse_calibration(self) -> list[dict[str, Any]]:
        labels = [f"{lo:.1f}-{hi:.1f}" for lo, hi in ADVERSE_BUCKETS]
        return [b.snapshot(lbl) for lbl, b in zip(labels, self.adverse_buckets)]

    def fill_probability_calibration(self) -> list[dict[str, Any]]:
        labels = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
        return [b.snapshot(lbl) for lbl, b in zip(labels, self.fill_prob_buckets)]

    def score_calibration(self) -> list[dict[str, Any]]:
        labels = ["0-40", "40-55", "55-70", "70-80", "80-100"]
        return [b.snapshot(lbl) for lbl, b in zip(labels, self.score_buckets)]

    def score_monotonicity_ok(self) -> bool:
        avgs: list[Decimal] = []
        for b in self.score_buckets:
            if b.fills <= 0:
                continue
            avgs.append(b.sum_net / Decimal(b.fills))
        if len(avgs) < 2:
            return True
        return all(avgs[i] <= avgs[i + 1] for i in range(len(avgs) - 1))

    def cancel_alpha_summary(self) -> dict[str, Any]:
        n = self.cancel_alpha_samples or 0
        alphas = [
            float(r.cancel_alpha_eur)
            for r in self.records
            if r.cancel_alpha_eur is not None
        ]
        return {
            "total_cancel_alpha_eur": str(self.sum_cancel_alpha.quantize(Decimal("0.01"))),
            "average_cancel_alpha_eur": str((self.sum_cancel_alpha / n).quantize(Decimal("0.01"))) if n else None,
            "median_cancel_alpha_eur": str(Decimal(str(statistics.median(alphas))).quantize(Decimal("0.01")))
            if alphas
            else None,
            "intelligence_cancels": self.intelligence_cancels,
            "avoided_adverse_loss_eur": str(self.sum_avoided_loss.quantize(Decimal("0.01"))),
            "missed_opportunity_eur": str(self.sum_missed_opportunity.quantize(Decimal("0.01"))),
            "samples": n,
        }

    def capital_lock_summary(self) -> dict[str, Any]:
        if not self.lock_times:
            return {
                "average_lock_seconds": None,
                "median_lock_seconds": None,
                "p95_lock_seconds": None,
            }
        sorted_lt = sorted(self.lock_times)
        p95_idx = min(len(sorted_lt) - 1, int(len(sorted_lt) * 0.95))
        return {
            "average_lock_seconds": str(Decimal(str(statistics.mean(sorted_lt))).quantize(Decimal("0.1"))),
            "median_lock_seconds": str(Decimal(str(statistics.median(sorted_lt))).quantize(Decimal("0.1"))),
            "p95_lock_seconds": str(Decimal(str(sorted_lt[p95_idx])).quantize(Decimal("0.1"))),
        }

    def top_groups(self, stats: dict[str, GroupStats], *, limit: int = 5) -> list[dict[str, Any]]:
        ranked = sorted(
            stats.values(),
            key=lambda g: _d(g.snapshot().get("net_per_capital_hour") or -999),
            reverse=True,
        )
        return [g.snapshot() for g in ranked[:limit]]

    def worst_capital_blockers(self, *, limit: int = 5) -> list[dict[str, Any]]:
        ranked = sorted(
            self.symbol_stats.values(),
            key=lambda g: g.sum_lock_seconds,
            reverse=True,
        )
        return [g.snapshot() for g in ranked[:limit]]

    def snapshot(self) -> dict[str, Any]:
        total_net = sum((r.realized_net or _ZERO for r in self.records if r.realized_net is not None), _ZERO)
        total_hold = sum(
            (r.hold_seconds or _ZERO for r in self.records if r.hold_seconds is not None),
            _ZERO,
        )
        total_cap = sum(
            (r.capital_deployed or _ZERO for r in self.records if r.capital_deployed is not None),
            _ZERO,
        )
        toxic_n = sum(1 for r in self.records if r.toxic_fill)
        fill_n = sum(1 for r in self.records if r.actual_fill)
        nph = net_per_hour(total_net, total_hold if total_hold > 0 else None)
        npch = net_per_capital_hour(total_net, total_cap, total_hold if total_hold > 0 else None)

        return {
            "attribution_records": len(self.records),
            "realized_net_eur": str(total_net.quantize(Decimal("0.01"))),
            "net_eur_per_hour": str(nph.quantize(Decimal("0.0001"))) if nph is not None else None,
            "net_eur_per_capital_hour": str(npch.quantize(Decimal("0.0001"))) if npch is not None else None,
            "toxic_fill_rate": str((Decimal(toxic_n) / Decimal(fill_n)).quantize(Decimal("0.001")))
            if fill_n
            else None,
            "intelligence_cancels": self.intelligence_cancels,
            **self.cancel_alpha_summary(),
            "maker_execution_alpha_avg": str(
                (self.maker_alpha_sum / self.maker_samples).quantize(Decimal("0.0001"))
            )
            if self.maker_samples
            else None,
            "taker_execution_alpha_avg": str(
                (self.taker_alpha_sum / self.taker_samples).quantize(Decimal("0.0001"))
            )
            if self.taker_samples
            else None,
            **self.capital_lock_summary(),
            "underwater_capital_eur": str(self.sum_underwater_capital.quantize(Decimal("0.01")))
            if self.underwater_samples
            else None,
            "shadow_threshold_cancels": dict(self.shadow_threshold_cancels),
            "score_monotonicity_ok": self.score_monotonicity_ok(),
            "top_symbols": self.top_groups(self.symbol_stats),
            "top_venues": self.top_groups(self.venue_stats),
            "top_strategies": self.top_groups(self.strategy_stats),
            "worst_capital_blockers": self.worst_capital_blockers(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [asdict(r) for r in self.records[-500:]],
            "shadow_threshold_cancels": self.shadow_threshold_cancels,
            "intelligence_cancels": self.intelligence_cancels,
            "sum_avoided_loss": str(self.sum_avoided_loss),
            "sum_missed_opportunity": str(self.sum_missed_opportunity),
            "sum_cancel_alpha": str(self.sum_cancel_alpha),
            "cancel_alpha_samples": self.cancel_alpha_samples,
            "lock_times": self.lock_times[-1000:],
            "sum_underwater_capital": str(self.sum_underwater_capital),
            "underwater_samples": self.underwater_samples,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> EconomicAttributionStore:
        store = cls()
        if not isinstance(raw, dict):
            return store
        store.shadow_threshold_cancels = dict(raw.get("shadow_threshold_cancels") or {})
        store.intelligence_cancels = int(raw.get("intelligence_cancels") or 0)
        store.sum_avoided_loss = _d(raw.get("sum_avoided_loss"))
        store.sum_missed_opportunity = _d(raw.get("sum_missed_opportunity"))
        store.sum_cancel_alpha = _d(raw.get("sum_cancel_alpha"))
        store.cancel_alpha_samples = int(raw.get("cancel_alpha_samples") or 0)
        store.lock_times = list(raw.get("lock_times") or [])
        store.sum_underwater_capital = _d(raw.get("sum_underwater_capital"))
        store.underwater_samples = int(raw.get("underwater_samples") or 0)
        return store

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path | str) -> EconomicAttributionStore:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return cls()
