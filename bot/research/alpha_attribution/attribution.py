"""Retained vs excluded feature-bucket comparison. Forensic, not a search."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Sequence

from bot.research.alpha_attribution.stability import classify_stability
from bot.research.accounting.waterfall import from_attached_events

_ZERO = Decimal("0")

FEATURE_KEYS = (
    "quote_age_regime",
    "spread_regime",
    "strength_regime",
    "liquidity_regime",
    "density_regime",
    "vol_regime",
    "symbol",
    "route",
    "session_utc",
    "side",
    "imbalance_regime",
)


def _econ(events: Sequence[dict[str, Any]], *, venue: str, venue_exit: str | None):
    n = len(events)
    if n == 0:
        return from_attached_events(
            [],
            venue=venue,
            venue_exit=venue_exit,
            mean_forward=None,
            audit={"candidates": 0, "admitted": 0, "rejected": 0},
        )
    mean_fwd = sum(float(e.get("forward") or 0.0) for e in events) / n
    return from_attached_events(
        events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_fwd,
        audit={"candidates": n, "admitted": n, "rejected": 0},
    )


def _sum_attached_net(events: Sequence[dict[str, Any]]) -> Decimal:
    tot = _ZERO
    for e in events:
        tot += Decimal(str(e.get("net") or 0))
    return tot


def feature_attribution(
    retained: Sequence[dict[str, Any]],
    excluded: Sequence[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    window_retained: Sequence[tuple[str, Sequence[dict[str, Any]]]],
    window_excluded: Sequence[tuple[str, Sequence[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in FEATURE_KEYS:
        buckets: set[str] = set()
        for e in list(retained) + list(excluded):
            buckets.add(str(e.get(feat) if e.get(feat) is not None else "UNKNOWN"))
        retained_by = defaultdict(list)
        excluded_by = defaultdict(list)
        for e in retained:
            retained_by[str(e.get(feat) if e.get(feat) is not None else "UNKNOWN")].append(e)
        for e in excluded:
            excluded_by[str(e.get(feat) if e.get(feat) is not None else "UNKNOWN")].append(e)
        for bucket in sorted(buckets):
            r = retained_by.get(bucket) or []
            x = excluded_by.get(bucket) or []
            re = _econ(r, venue=venue, venue_exit=venue_exit)
            xe = _econ(x, venue=venue, venue_exit=venue_exit)
            x_wnets = []
            r_wnets = []
            for _wid, we in window_excluded:
                sub = [
                    e
                    for e in we
                    if str(e.get(feat) if e.get(feat) is not None else "UNKNOWN") == bucket
                ]
                x_wnets.append(_sum_attached_net(sub) if sub else _ZERO)
            for _wid, we in window_retained:
                sub = [
                    e
                    for e in we
                    if str(e.get(feat) if e.get(feat) is not None else "UNKNOWN") == bucket
                ]
                r_wnets.append(_sum_attached_net(sub) if sub else _ZERO)
            xstab = classify_stability(events=x, window_nets=x_wnets)
            rstab = classify_stability(events=r, window_nets=r_wnets)
            diff_net = xe.replay_net.value - re.replay_net.value
            rows.append(
                {
                    "feature": feat,
                    "bucket": bucket,
                    "pre_trade_available": True,
                    "retained": {
                        "signal_count": re.signals.value,
                        "fill_count": re.fills.value,
                        "replay_net_eur": str(re.replay_net.value),
                        "replay_net_per_signal": None
                        if re.signals.value == 0
                        else str(re.replay_net_per_signal.value),
                        "replay_net_per_fill": None
                        if re.replay_net_per_fill is None
                        else str(re.replay_net_per_fill.value),
                    },
                    "excluded": {
                        "signal_count": xe.signals.value,
                        "fill_count": xe.fills.value,
                        "replay_net_eur": str(xe.replay_net.value),
                        "replay_net_per_signal": None
                        if xe.signals.value == 0
                        else str(xe.replay_net_per_signal.value),
                        "replay_net_per_fill": None
                        if xe.replay_net_per_fill is None
                        else str(xe.replay_net_per_fill.value),
                    },
                    "difference": {
                        "excluded_minus_retained_replay_net_eur": str(diff_net),
                        "excluded_signal_count_minus_retained": xe.signals.value - re.signals.value,
                    },
                    "economic_contribution": str(xe.replay_net.value),
                    "excluded_replay_net_eur": str(xe.replay_net.value),
                    "window_stability": xstab["stability"],
                    "stability": xstab["stability"],
                    "excluded_positive_windows": xstab["positive_windows"],
                    "excluded_negative_windows": xstab["negative_windows"],
                    "candidate_hypothesis_usefulness": (
                        "FORENSIC_ONLY — not a threshold. DEV freeze required before any child hypothesis."
                    ),
                    "usefulness": "FORENSIC_ONLY",
                    "note": (
                        f"{feat}={bucket}: retained n={re.signals.value} net={re.replay_net.value}; "
                        f"excluded n={xe.signals.value} net={xe.replay_net.value}"
                    ),
                }
            )
    rows.sort(
        key=lambda r: abs(Decimal(str(r["excluded"]["replay_net_eur"] or 0))),
        reverse=True,
    )
    return rows
