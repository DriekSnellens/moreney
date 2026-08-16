"""Shared gated lifecycle for research families."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from bot.research.tournament.contract import CandidateResult, SignalStats, StrategyResearchCandidate
from bot.research.tournament.economics import execution_replay_net, net_waterfall_from_edge
from bot.research.tournament.freeze import assert_params_unchanged, freeze_experiment
from bot.research.tournament.gates import (
    classify_oos,
    has_predictive_signal,
    sample_adequate_dev,
    sample_adequate_oos,
    summarize_forwards,
    supported_horizons_from_readiness,
)
from bot.research.tournament.criteria import MAX_TOP_ROUTE_PNL_SHARE, MAX_TOP_SYMBOL_PNL_SHARE
from bot.research.tournament.tape_index import TapeIndex


EvalFn = Callable[..., tuple[SignalStats, list[dict[str, Any]], dict[str, Any]]]


class GatedFamily(StrategyResearchCandidate):
    """Template: data → signal → fit → freeze → OOS → econ → exec → stability."""

    strategy_id = "base"
    _features: tuple[str, ...] = ()
    _requested: tuple[int, ...] = (500, 1000, 2000, 5000)

    def required_horizons(self) -> Sequence[int]:
        return self._requested

    def required_features(self) -> Sequence[str]:
        return self._features

    def evaluate_window(
        self,
        index: TapeIndex,
        *,
        start_ns: int,
        end_ns_exclusive: int | None,
        end_ns_inclusive: int | None,
        params: dict[str, Any],
        horizons: list[int],
    ) -> tuple[SignalStats, list[dict[str, Any]]]:
        raise NotImplementedError

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def run(
        self,
        *,
        index: TapeIndex,
        split: dict[str, Any],
        horizon_readiness: dict[str, str],
        dataset_meta: dict[str, Any],
    ) -> CandidateResult:
        notes: list[str] = []
        requested = list(self.required_horizons())
        supported, unsupported, h_reason = supported_horizons_from_readiness(
            requested, horizon_readiness
        )
        empty = SignalStats()
        if not supported:
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="DATA_UNSUPPORTED",
                failed_gate="DATA",
                requested_horizons=requested,
                supported_horizons=[],
                horizon_reason=h_reason,
                frozen_params={},
                dev_stats=empty,
                oos_stats=None,
                oos_class=None,
                expected_gross=None,
                expected_net=None,
                execution_net=None,
                waterfall={},
                stability={},
                tournament_score=0.0,
                experiment_id=None,
                notes=[
                    f"REQUESTED_HORIZON={requested}",
                    f"SUPPORTED_HORIZON=[]",
                    f"REASON={h_reason}",
                ],
            )

        if not split.get("available"):
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="INSUFFICIENT_SAMPLE",
                failed_gate="DATA",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason="split_unavailable",
                frozen_params={},
                dev_stats=empty,
                oos_stats=None,
                oos_class=None,
                expected_gross=None,
                expected_net=None,
                execution_net=None,
                waterfall={},
                stability={},
                tournament_score=0.0,
                experiment_id=None,
                notes=["chronological split unavailable"],
            )

        dev_w = split["development"]
        oos_w = split["untouched_oos"]
        freeze_w = split["freeze_boundary"]

        # --- DEVELOPMENT fit on small grid ---
        best_params: dict[str, Any] | None = None
        best_stats = empty
        best_events: list[dict[str, Any]] = []
        best_score = float("-inf")
        for params in self.param_grid(supported):
            stats, events = self.evaluate_window(
                index,
                start_ns=dev_w["start_ts_ns"],
                end_ns_exclusive=dev_w["end_ts_ns_exclusive"],
                end_ns_inclusive=None,
                params=params,
                horizons=supported,
            )
            # Predeclared objective: robust expected conditional forward return
            # with uncertainty penalty (prefer CI away from 0).
            mean = stats.conditional_forward_mean or 0.0
            half = 0.0
            if stats.ci_low is not None and stats.ci_high is not None:
                half = abs(stats.ci_high - stats.ci_low) / 2.0
            score = abs(mean) - 0.5 * half
            if stats.signals < 10:
                score = float("-inf")
            if score > best_score:
                best_score = score
                best_params = dict(params)
                best_stats = stats
                best_events = events

        if best_params is None:
            best_params = self.param_grid(supported)[0]

        if not sample_adequate_dev(best_stats):
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="INSUFFICIENT_SAMPLE",
                failed_gate="DEVELOPMENT",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason=h_reason,
                frozen_params=best_params,
                dev_stats=best_stats,
                oos_stats=None,
                oos_class=None,
                expected_gross=None,
                expected_net=None,
                execution_net=None,
                waterfall={},
                stability={},
                tournament_score=0.0,
                experiment_id=None,
                notes=notes + ["development sample inadequate"],
            )

        if not has_predictive_signal(best_stats):
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="NO_SIGNAL",
                failed_gate="SIGNAL",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason=h_reason,
                frozen_params=best_params,
                dev_stats=best_stats,
                oos_stats=None,
                oos_class=None,
                expected_gross=None,
                expected_net=None,
                execution_net=None,
                waterfall={},
                stability={},
                tournament_score=0.0,
                experiment_id=None,
                notes=notes + ["no predictive separation on DEVELOPMENT"],
            )

        frozen = freeze_experiment(
            strategy_id=self.strategy_id,
            dataset_id=index.dataset_id,
            dataset_fingerprint=index.content_fingerprint,
            parameters=best_params,
            development_window=dev_w,
            freeze_boundary=freeze_w,
            oos_window=oos_w,
            feature_definitions=list(self.required_features()),
        )
        assert_params_unchanged(frozen, best_params)

        # --- UNTOUCHED OOS (exactly once) ---
        oos_stats, oos_events = self.evaluate_window(
            index,
            start_ns=oos_w["start_ts_ns"],
            end_ns_exclusive=None,
            end_ns_inclusive=oos_w["end_ts_ns_inclusive"],
            params=best_params,
            horizons=supported,
        )
        if not sample_adequate_oos(oos_stats):
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="INSUFFICIENT_SAMPLE",
                failed_gate="OOS",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason=h_reason,
                frozen_params=best_params,
                dev_stats=best_stats,
                oos_stats=oos_stats,
                oos_class=classify_oos(best_stats, oos_stats),
                expected_gross=None,
                expected_net=None,
                execution_net=None,
                waterfall={},
                stability={},
                tournament_score=0.0,
                experiment_id=frozen["experiment_id"],
                notes=notes + ["OOS sample inadequate"],
            )

        oos_class = classify_oos(best_stats, oos_stats)
        if oos_class in {"DISAPPEARED", "REVERSED"} or not has_predictive_signal(oos_stats):
            why = f"oos_class={oos_class}"
            if not has_predictive_signal(oos_stats):
                why += "; oos_signal_gate_failed"
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="OOS_FAILED",
                failed_gate="OOS",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason=h_reason,
                frozen_params=best_params,
                dev_stats=best_stats,
                oos_stats=oos_stats,
                oos_class=oos_class,
                expected_gross=None,
                expected_net=None,
                execution_net=None,
                waterfall={},
                stability={},
                tournament_score=0.0,
                experiment_id=frozen["experiment_id"],
                notes=notes + [why],
            )

        # --- NET ECONOMICS ---
        edge = abs(oos_stats.conditional_forward_mean or 0.0)
        venue = str(best_params.get("venue") or best_params.get("leader") or "binance")
        venue_exit = best_params.get("follower") or best_params.get("venue_exit")
        waterfall = net_waterfall_from_edge(
            gross_edge_fraction=edge,
            venue=venue,
            venue_exit=venue_exit,
        )
        expected_net = float(waterfall["EXPECTED_NET"])
        expected_gross = float(waterfall["GROSS_PREDICTIVE_EDGE"])
        if expected_net <= 0:
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="COST_NEGATIVE",
                failed_gate="ECONOMICS",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason=h_reason,
                frozen_params=best_params,
                dev_stats=best_stats,
                oos_stats=oos_stats,
                oos_class=oos_class,
                expected_gross=expected_gross,
                expected_net=expected_net,
                execution_net=None,
                waterfall=waterfall,
                stability={},
                tournament_score=0.0,
                experiment_id=frozen["experiment_id"],
                notes=notes + ["EXPECTED_NET <= 0"],
            )

        # --- EXECUTION REPLAY ---
        replay = execution_replay_net(expected_net=expected_net)
        exec_net = float(replay["EXECUTION_NET"])
        waterfall = {**waterfall, "execution_replay": replay}
        if exec_net <= 0:
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="EXECUTION_NEGATIVE",
                failed_gate="EXECUTION",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason=h_reason,
                frozen_params=best_params,
                dev_stats=best_stats,
                oos_stats=oos_stats,
                oos_class=oos_class,
                expected_gross=expected_gross,
                expected_net=expected_net,
                execution_net=exec_net,
                waterfall=waterfall,
                stability={},
                tournament_score=0.0,
                experiment_id=frozen["experiment_id"],
                notes=notes + ["realistic replay NET <= 0"],
            )

        # --- STABILITY ---
        stability = _stability(oos_events)
        if stability.get("concentrated"):
            return CandidateResult(
                strategy_id=self.strategy_id,
                verdict="UNSTABLE",
                failed_gate="STABILITY",
                requested_horizons=requested,
                supported_horizons=supported,
                horizon_reason=h_reason,
                frozen_params=best_params,
                dev_stats=best_stats,
                oos_stats=oos_stats,
                oos_class=oos_class,
                expected_gross=expected_gross,
                expected_net=expected_net,
                execution_net=exec_net,
                waterfall=waterfall,
                stability=stability,
                tournament_score=0.0,
                experiment_id=frozen["experiment_id"],
                notes=notes + ["CONCENTRATED_RESULT"],
            )

        return CandidateResult(
            strategy_id=self.strategy_id,
            verdict="PAPER_CANDIDATE",
            failed_gate=None,
            requested_horizons=requested,
            supported_horizons=supported,
            horizon_reason=h_reason,
            frozen_params=best_params,
            dev_stats=best_stats,
            oos_stats=oos_stats,
            oos_class=oos_class,
            expected_gross=expected_gross,
            expected_net=expected_net,
            execution_net=exec_net,
            waterfall=waterfall,
            stability=stability,
            tournament_score=0.0,
            experiment_id=frozen["experiment_id"],
            notes=notes
            + [
                "RESEARCH CANDIDATE — NOT PROVEN LIVE PROFITABLE",
                f"frozen={frozen['experiment_fingerprint'][:16]}",
            ],
        )


def _stability(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"label": "EMPTY", "stability_score": 0.0, "concentrated": True}
    by_sym: dict[str, float] = {}
    by_route: dict[str, float] = {}
    for e in events:
        edge = abs(float(e.get("forward") or 0))
        sym = str(e.get("symbol") or "?")
        route = str(e.get("route") or e.get("venue") or "?")
        by_sym[sym] = by_sym.get(sym, 0.0) + edge
        by_route[route] = by_route.get(route, 0.0) + edge
    tot_s = sum(by_sym.values()) or 1.0
    tot_r = sum(by_route.values()) or 1.0
    top_s = max(by_sym.values()) / tot_s if by_sym else 1.0
    top_r = max(by_route.values()) / tot_r if by_route else 1.0
    concentrated = top_s > MAX_TOP_SYMBOL_PNL_SHARE or top_r > MAX_TOP_ROUTE_PNL_SHARE
    score = max(0.0, 1.0 - max(top_s, top_r))
    return {
        "label": "CONCENTRATED_RESULT" if concentrated else "DIVERSIFIED",
        "top_symbol_share": top_s,
        "top_route_share": top_r,
        "stability_score": score,
        "concentrated": concentrated,
        "by_symbol": by_sym,
        "by_route": by_route,
    }
