"""Synthetic mark paths + bag seeds from live bridge state."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BagSeed:
    key: str
    qty: float
    cost: float
    start_mark: float


def load_bag_seeds(
    bridge_path: Path | None = None,
    *,
    min_notional_eur: float = 25.0,
) -> list[BagSeed]:
    path = bridge_path or Path("./data/live_micro_bridge_state.json")
    if not path.exists():
        return _fallback_seeds()
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    trail = raw.get("trail") or {}
    seeds: list[BagSeed] = []
    for key, st in trail.items():
        if not isinstance(st, dict):
            continue
        base = str(st.get("base") or key.split(":")[-1]).upper()
        if base in {"ETH", "BTC"}:
            continue
        try:
            cost = float(st.get("cost") or 0)
            mark = float(st.get("last_mark") or 0)
        except (TypeError, ValueError):
            continue
        if cost <= 0 or mark <= 0:
            continue
        # Prefer session lot qty sum when present.
        qty = _qty_from_lots(raw.get("session_lots") or {}, key)
        if qty <= 0:
            continue
        if qty * mark < min_notional_eur:
            continue
        seeds.append(BagSeed(key=key, qty=qty, cost=cost, start_mark=mark))
    return seeds or _fallback_seeds()


def _qty_from_lots(lots: dict[str, Any], key: str) -> float:
    rows = lots.get(key) or []
    total = 0.0
    if isinstance(rows, list):
        for row in rows:
            try:
                if isinstance(row, (list, tuple)) and row:
                    total += float(row[0])
                elif isinstance(row, dict):
                    total += float(row.get("qty") or row.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
    return total


def _fallback_seeds() -> list[BagSeed]:
    return [
        BagSeed("okx:SOL", qty=4.8, cost=85.0, start_mark=83.0),
        BagSeed("bitvavo:FET", qty=3500.0, cost=0.151, start_mark=0.142),
        BagSeed("bitvavo:APT", qty=260.0, cost=0.535, start_mark=0.490),
        BagSeed("okx:ARB", qty=1600.0, cost=0.083, start_mark=0.079),
        BagSeed("bitvavo:LTC", qty=3.5, cost=45.1, start_mark=43.0),
    ]


def make_path(
    start: float,
    *,
    n: int,
    kind: str,
    seed: int,
) -> list[float]:
    """Deterministic synthetic mark path."""
    rng = random.Random(seed)
    px = float(start)
    out = [px]
    if kind == "mean_revert_bounce":
        # Drift back toward +4% then chop — recovery scenario.
        target = start * 1.04
        for i in range(1, n):
            pull = (target - px) * 0.04
            shock = rng.gauss(0.0, 0.004)
            px = max(px * 1e-6, px + pull + px * shock)
            if i > n * 0.6:
                target = start * 1.01
            out.append(px)
    elif kind == "pump_dump":
        # Rise to +8%, then dump to -3%.
        for i in range(1, n):
            t = i / n
            if t < 0.45:
                drift = 0.0018
            elif t < 0.55:
                drift = 0.0002
            else:
                drift = -0.0022
            px = max(px * 1e-6, px * (1.0 + drift + rng.gauss(0.0, 0.003)))
            out.append(px)
    elif kind == "slow_grind_up":
        for i in range(1, n):
            px = max(px * 1e-6, px * (1.0 + 0.00035 + rng.gauss(0.0, 0.002)))
            out.append(px)
    elif kind == "choppy_flat":
        for _ in range(1, n):
            px = max(px * 1e-6, px * (1.0 + rng.gauss(0.0, 0.0035)))
            out.append(px)
    else:  # gbm_mild
        mu, sigma = 0.00015, 0.004
        for _ in range(1, n):
            z = rng.gauss(0.0, 1.0)
            px = max(px * 1e-6, px * math.exp((mu - 0.5 * sigma * sigma) + sigma * z))
            out.append(px)
    return out


PATH_KINDS = (
    "mean_revert_bounce",
    "pump_dump",
    "slow_grind_up",
    "choppy_flat",
    "gbm_mild",
)


def build_scenario_paths(
    bags: list[BagSeed],
    *,
    n_ticks: int = 800,
    base_seed: int = 42,
) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for bi, bag in enumerate(bags):
        for ki, kind in enumerate(PATH_KINDS):
            marks = make_path(
                bag.start_mark, n=n_ticks, kind=kind, seed=base_seed + bi * 17 + ki * 91
            )
            scenarios.append(
                {
                    "bag": bag,
                    "kind": kind,
                    "marks": marks,
                    "seed": base_seed + bi * 17 + ki * 91,
                }
            )
    return scenarios
