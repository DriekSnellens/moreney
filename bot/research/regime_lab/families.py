"""Independent regime-gated families. Parents are wrapped, not edited."""

from __future__ import annotations

from typing import Any

from bot.research.forensics.buckets import QUOTE_AGE_FRESH_MS, SPREAD_WIDE_BPS
from bot.research.regime_lab.stability import stability_report
from bot.research.regime_lab.features import enrich_pretrade, views_for
from bot.research.regime_lab.protocol import H0005, H0007, HORIZONS_MS
from bot.research.tournament.base import GatedFamily
from bot.research.tournament.contract import SignalStats
from bot.research.tournament.families import (
    CrossVenueDislocationFamily,
    ShortHorizonMeanReversionFamily,
)
from bot.research.tournament.gates import summarize_forwards
from bot.research.tournament.tape_index import TapeIndex, forward_return, iter_window


def classify_freshness(age: float | None) -> str:
    if age is None:
        return "UNSUPPORTED_DATA"
    if float(age) < QUOTE_AGE_FRESH_MS:
        return "ADMITTED"
    return "REJECTED"


def classify_wide_spread(spread_bps: float | None) -> str:
    if spread_bps is None:
        return "UNSUPPORTED_DATA"
    if float(spread_bps) >= SPREAD_WIDE_BPS:
        return "ADMITTED"
    return "REJECTED"


class _AuditFamily(GatedFamily):
    hypothesis_id = ""
    parent_hypothesis_id = ""
    last_audit: dict[str, Any]

    def __init__(self) -> None:
        self.last_audit = {
            "candidates": 0,
            "admitted": 0,
            "rejected": 0,
            "unsupported": 0,
            "rejected_not_labels": True,
        }

    def stability_of(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        return stability_report(events)


class FreshnessCVDFamily(_AuditFamily):
    strategy_id = "cross_venue_dislocation_freshness"
    hypothesis_id = "H-0005"
    parent_hypothesis_id = "H-0001"
    _features = tuple(H0005["pre_trade_features"])
    _requested = HORIZONS_MS

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        return CrossVenueDislocationFamily().param_grid(horizons)

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
        parent = CrossVenueDislocationFamily()
        _stats, candidates = parent.evaluate_window(
            index,
            start_ns=start_ns,
            end_ns_exclusive=end_ns_exclusive,
            end_ns_inclusive=end_ns_inclusive,
            params=params,
            horizons=horizons,
        )
        views = views_for(index)
        venue = str(params.get("venue_a") or "binance")
        peer = str(params.get("venue_b") or "bitvavo")
        admitted: list[dict[str, Any]] = []
        rejected = unsupported = 0
        forwards: list[float] = []
        for raw in candidates:
            row = enrich_pretrade(
                raw, index=index, views=views, venue=venue, peer_venue=peer
            )
            age = row.get("quote_age_ms")
            decision = classify_freshness(None if age is None else float(age))
            row["admission"] = decision
            if decision == "UNSUPPORTED_DATA":
                unsupported += 1
                continue
            if decision == "ADMITTED":
                admitted.append(row)
                forwards.append(float(row["forward"]))
            else:
                rejected += 1
        self.last_audit = {
            "candidates": len(candidates),
            "admitted": len(admitted),
            "rejected": rejected,
            "unsupported": unsupported,
            "rejected_not_labels": True,
            "hypothesis_id": self.hypothesis_id,
        }
        obs = max(_stats.observations, len(candidates))
        return summarize_forwards(forwards, observations=obs), admitted


class WideSpreadMRFamily(_AuditFamily):
    strategy_id = "short_horizon_mean_reversion_wide_spread"
    hypothesis_id = "H-0007"
    parent_hypothesis_id = "H-0003"
    _features = tuple(H0007["pre_trade_features"])
    _requested = HORIZONS_MS

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        return ShortHorizonMeanReversionFamily().param_grid(horizons)

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
        parent = ShortHorizonMeanReversionFamily()
        _stats, candidates = parent.evaluate_window(
            index,
            start_ns=start_ns,
            end_ns_exclusive=end_ns_exclusive,
            end_ns_inclusive=end_ns_inclusive,
            params=params,
            horizons=horizons,
        )
        views = views_for(index)
        venue = str(params.get("venue") or "bitvavo")
        others = [v for v in ("binance", "okx", "bitvavo") if v != venue]
        peer = others[0] if others else None
        admitted: list[dict[str, Any]] = []
        rejected = unsupported = 0
        forwards: list[float] = []
        for raw in candidates:
            row = enrich_pretrade(
                raw, index=index, views=views, venue=venue, peer_venue=peer
            )
            sp = row.get("spread_bps")
            decision = classify_wide_spread(None if sp is None else float(sp))
            row["admission"] = decision
            if decision == "UNSUPPORTED_DATA":
                unsupported += 1
                continue
            if decision == "ADMITTED":
                admitted.append(row)
                forwards.append(float(row["forward"]))
            else:
                rejected += 1
        self.last_audit = {
            "candidates": len(candidates),
            "admitted": len(admitted),
            "rejected": rejected,
            "unsupported": unsupported,
            "rejected_not_labels": True,
            "hypothesis_id": self.hypothesis_id,
            "event_density_is_feature_only": True,
        }
        obs = max(_stats.observations, len(candidates))
        return summarize_forwards(forwards, observations=obs), admitted


class NoTradeBaseline(_AuditFamily):
    strategy_id = "no_trade_baseline"
    hypothesis_id = "CTRL-NO-TRADE"
    _features = ()
    _requested = HORIZONS_MS

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        return [{"horizon_ms": horizons[0] if horizons else 500}]

    def evaluate_window(self, index, **kwargs) -> tuple[SignalStats, list[dict[str, Any]]]:
        self.last_audit = {
            "candidates": 0,
            "admitted": 0,
            "rejected": 0,
            "unsupported": 0,
            "note": "no-trade control; NET=0",
        }
        return SignalStats(observations=0, signals=0), []


class RegimeOnlyDescriptive(_AuditFamily):
    """Unconditional forward in the regime — no dislocation/MR sign.

    Descriptive control: does the regime itself have drift?
    """

    strategy_id = "regime_only_descriptive"
    hypothesis_id = "CTRL-REGIME-ONLY"
    _features = ("spread", "quote_staleness")
    _requested = HORIZONS_MS
    regime: str = "fresh"

    def __init__(self, *, regime: str = "fresh") -> None:
        super().__init__()
        self.regime = regime
        self.strategy_id = f"regime_only_{regime}"

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        h = horizons[0] if horizons else 500
        if self.regime == "fresh":
            return [{"horizon_ms": h, "venue_a": "binance", "venue_b": "bitvavo"}]
        return [{"horizon_ms": h, "venue": "bitvavo"}]

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
        from bot.research.tournament.families import _align_follower, _ns

        h_ns = _ns(int(params.get("horizon_ms") or 500))
        forwards: list[float] = []
        events: list[dict[str, Any]] = []
        obs = 0
        if self.regime == "fresh":
            a, b = "binance", "bitvavo"
            for symbol in sorted(set(index.symbols_for(a)) & set(index.symbols_for(b))):
                pa = index.points(a, symbol)
                pb = index.points(b, symbol)
                window = list(
                    iter_window(
                        pa,
                        start_ns=start_ns,
                        end_ns_exclusive=end_ns_exclusive,
                        end_ns_inclusive=end_ns_inclusive,
                    )
                )
                if len(window) < 10 or len(pb) < 10:
                    continue
                ts_to_i = {p.ts_ns: i for i, p in enumerate(pa)}
                hint = 0
                step = max(1, len(window) // 2000)
                for k in range(0, len(window), step):
                    p = window[k]
                    obs += 1
                    jb, hint = _align_follower(p.ts_ns, pb, i_hint=hint)
                    if jb is None:
                        continue
                    age = (p.ts_ns - pb[jb].ts_ns) / 1e6
                    if age < 0:
                        continue
                    if age >= QUOTE_AGE_FRESH_MS:
                        continue
                    i = ts_to_i[p.ts_ns]
                    fwd = forward_return(pa, i, h_ns)
                    if fwd is None:
                        continue
                    forwards.append(fwd)
                    events.append(
                        {
                            "symbol": symbol,
                            "route": f"{a}|{b}",
                            "forward": fwd,
                            "ts_ns": p.ts_ns,
                            "admission": "ADMITTED",
                            "quote_age_ms": age,
                        }
                    )
        else:
            venue = "bitvavo"
            for symbol in index.symbols_for(venue):
                pts = index.points(venue, symbol)
                window = list(
                    iter_window(
                        pts,
                        start_ns=start_ns,
                        end_ns_exclusive=end_ns_exclusive,
                        end_ns_inclusive=end_ns_inclusive,
                    )
                )
                ts_to_i = {p.ts_ns: i for i, p in enumerate(pts)}
                step = max(1, len(window) // 2000)
                for k in range(0, len(window), step):
                    p = window[k]
                    obs += 1
                    sp = (p.ask - p.bid) / p.mid * 10000.0 if p.mid > 0 else None
                    if sp is None or sp < SPREAD_WIDE_BPS:
                        continue
                    i = ts_to_i[p.ts_ns]
                    fwd = forward_return(pts, i, h_ns)
                    if fwd is None:
                        continue
                    forwards.append(fwd)
                    events.append(
                        {
                            "symbol": symbol,
                            "route": venue,
                            "forward": fwd,
                            "ts_ns": p.ts_ns,
                            "admission": "ADMITTED",
                            "spread_bps": sp,
                        }
                    )
        self.last_audit = {
            "candidates": obs,
            "admitted": len(events),
            "rejected": max(0, obs - len(events)),
            "unsupported": 0,
            "descriptive": True,
        }
        return summarize_forwards(forwards, observations=max(obs, len(forwards))), events


def gated_families() -> list[_AuditFamily]:
    return [FreshnessCVDFamily(), WideSpreadMRFamily()]
