"""Window / concentration stability labels. Reuses existing caps. No new cuts."""

from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Any, Sequence

from bot.research.alpha_attribution.protocol import (
    MIN_SIGNALS,
    MIN_WINDOWS_WITH_SIGNALS,
    ROUTE_SHARE_CAP,
    SYMBOL_SHARE_CAP,
)
from bot.research.regime_lab.stability import stability_report

_ZERO = Decimal("0")


def classify_stability(
    *,
    events: Sequence[dict[str, Any]],
    window_nets: Sequence[Decimal],
    oos_start_ns: int | None = None,
    oos_end_ns: int | None = None,
) -> dict[str, Any]:
    n = len(events)
    nets = [Decimal(str(x)) for x in window_nets]
    pos = sum(1 for x in nets if x > 0)
    neg = sum(1 for x in nets if x < 0)
    zero = len(nets) - pos - neg
    n_win = sum(1 for x in nets if x != 0) or len(nets)
    stab = stability_report(
        list(events), oos_start_ns=oos_start_ns, oos_end_ns=oos_end_ns
    )
    concentrated = bool(stab.get("concentrated"))
    top_sym = float(stab.get("top_symbol_share") or 0.0)
    top_route = float(stab.get("top_route_share") or 0.0)
    route_limited = bool(stab.get("ROUTE_UNIVERSE_LIMITED"))

    if n < MIN_SIGNALS or len(nets) < MIN_WINDOWS_WITH_SIGNALS:
        label = "INSUFFICIENT_DATA"
    elif concentrated or top_sym > SYMBOL_SHARE_CAP or (
        (not route_limited) and top_route > ROUTE_SHARE_CAP
    ):
        label = "UNSTABLE"
    elif pos > 0 and neg > 0:
        label = "MIXED"
    elif pos >= MIN_WINDOWS_WITH_SIGNALS and neg == 0:
        label = "STABLE"
    else:
        # All-negative or too few positive windows: not an alpha source.
        label = "UNSTABLE" if neg >= pos else "MIXED"

    mean_n = (sum(nets, _ZERO) / Decimal(len(nets))) if nets else None
    med_n = Decimal(str(median(nets))) if nets else None
    tot = sum((abs(x) for x in nets), _ZERO) or Decimal("1")
    top_win = (max((abs(x) for x in nets), default=_ZERO) / tot) if nets else None
    return {
        "stability": label,
        "signal_count": n,
        "window_nets": [str(x) for x in nets],
        "positive_windows": pos,
        "negative_windows": neg,
        "zero_windows": zero,
        "n_windows": len(nets),
        "windows_with_nonzero_net": n_win,
        "mean_window_net": None if mean_n is None else str(mean_n),
        "median_window_net": None if med_n is None else str(med_n),
        "top_symbol_share": top_sym,
        "top_route_share": top_route,
        "top_symbol": stab.get("top_symbol"),
        "top_route": stab.get("top_route"),
        "ROUTE_UNIVERSE_LIMITED": route_limited,
        "concentrated": concentrated,
        "top_window_share": None if top_win is None else float(top_win),
        "min_signals_floor": MIN_SIGNALS,
        "min_windows_floor": MIN_WINDOWS_WITH_SIGNALS,
        "symbol_share_cap": SYMBOL_SHARE_CAP,
        "route_share_cap": ROUTE_SHARE_CAP,
        "criteria_relaxed": False,
        "note": (
            "STABLE requires existing 70% concentration caps, "
            f">={MIN_SIGNALS} signals, >={MIN_WINDOWS_WITH_SIGNALS} windows, "
            "and no negative windows. Not an alpha claim."
        ),
    }
