"""Fill toxicity buckets from future markout (analysis / reporting only).

Buckets are assigned from *observed* 5s adverse bps after the fill.
They never feed the live ranker (would be look-ahead).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

_ZERO = Decimal("0")

# Thresholds in adverse bps (positive = mid moved against the fill).
BUCKETS: tuple[tuple[str, Decimal | None, Decimal | None], ...] = (
    ("very_favorable", None, Decimal("-5")),       # adverse < -5
    ("neutral", Decimal("-5"), Decimal("5")),      # -5 <= adverse < 5
    ("toxic", Decimal("5"), Decimal("20")),        # 5 <= adverse < 20
    ("very_toxic", Decimal("20"), None),           # adverse >= 20
)


def classify_markout_bps(adverse_bps: Decimal) -> str:
    for name, lo, hi in BUCKETS:
        if lo is not None and adverse_bps < lo:
            continue
        if hi is not None and adverse_bps >= hi:
            continue
        if lo is None and hi is not None and adverse_bps < hi:
            return name
        if hi is None and lo is not None and adverse_bps >= lo:
            return name
        if lo is not None and hi is not None and lo <= adverse_bps < hi:
            return name
    return "neutral"


def toxicity_report(
    samples: Iterable[dict[str, Any]],
    *,
    markout_key: str = "markout_bps_5s",
) -> dict[str, Any]:
    """Aggregate fill samples into toxicity buckets and slice dimensions.

    Each sample may include: venue, symbol, side, strategy, volatility_regime,
    spread_bps, inventory_state, p_fill, markout_bps_5s, realized_net.
    """
    by_bucket: dict[str, dict[str, Any]] = {
        name: {"n": 0, "sum_markout": _ZERO, "sum_p_fill": _ZERO, "sum_realized": _ZERO}
        for name, _, _ in BUCKETS
    }
    slices: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {"n": 0, "sum_markout": _ZERO, "sum_p_fill": _ZERO, "toxic_n": 0}
        )
    )
    pairs: list[tuple[float, float]] = []  # (p_fill, markout)

    for raw in samples:
        try:
            m = Decimal(str(raw.get(markout_key, "0") or 0))
        except Exception:
            continue
        bucket = classify_markout_bps(m)
        row = by_bucket[bucket]
        row["n"] += 1
        row["sum_markout"] += m
        p_fill = Decimal(str(raw.get("p_fill", "0") or 0))
        row["sum_p_fill"] += p_fill
        row["sum_realized"] += Decimal(str(raw.get("realized_net", "0") or 0))
        pairs.append((float(p_fill), float(m)))

        dims = {
            "venue": str(raw.get("venue") or "unknown"),
            "symbol": str(raw.get("symbol") or "unknown"),
            "side": str(raw.get("side") or "unknown"),
            "strategy": str(raw.get("strategy") or "unknown"),
            "volatility_regime": str(raw.get("volatility_regime") or "unknown"),
            "spread_bucket": _spread_bucket(raw.get("spread_bps")),
            "inventory_state": str(raw.get("inventory_state") or "unknown"),
        }
        for dim, value in dims.items():
            cell = slices[dim][value]
            cell["n"] += 1
            cell["sum_markout"] += m
            cell["sum_p_fill"] += p_fill
            if bucket in {"toxic", "very_toxic"}:
                cell["toxic_n"] += 1

    def _finalize_bucket(b: dict[str, Any]) -> dict[str, Any]:
        n = int(b["n"])
        return {
            "n": n,
            "avg_markout_bps": str(b["sum_markout"] / n) if n else "0",
            "avg_p_fill": str(b["sum_p_fill"] / n) if n else "0",
            "sum_realized_net": str(b["sum_realized"]),
        }

    corr = _corr([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 3 else None

    slice_out: dict[str, list[dict[str, Any]]] = {}
    for dim, values in slices.items():
        rows = []
        for value, cell in sorted(values.items(), key=lambda kv: -kv[1]["n"]):
            n = int(cell["n"])
            rows.append(
                {
                    "key": value,
                    "n": n,
                    "avg_markout_bps": str(cell["sum_markout"] / n) if n else "0",
                    "avg_p_fill": str(cell["sum_p_fill"] / n) if n else "0",
                    "toxic_rate": str(Decimal(cell["toxic_n"]) / Decimal(n)) if n else "0",
                }
            )
        slice_out[dim] = rows

    return {
        "buckets": {name: _finalize_bucket(by_bucket[name]) for name, _, _ in BUCKETS},
        "p_fill_vs_markout_corr": corr,
        "hypothesis": (
            "If corr(p_fill, markout) > 0, higher fill probability selects worse toxicity"
        ),
        "slices": slice_out,
        "sample_count": len(pairs),
        "kind": "observed",
    }


def _spread_bucket(raw: object) -> str:
    try:
        bps = float(raw or 0)
    except Exception:
        return "unknown"
    if bps < 5:
        return "tight_<5"
    if bps < 15:
        return "mid_5_15"
    return "wide_>=15"


def _corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = sum((x - mx) ** 2 for x in xs) ** 0.5
    deny = sum((y - my) ** 2 for y in ys) ** 0.5
    if denx <= 0 or deny <= 0:
        return None
    return num / (denx * deny)
