"""Generic clip exit overlays — no per-coin branches."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitPolicy:
    name: str
    sma_n: int = 50
    alt_frac: float = 0.25
    ignore_sma_flatten: bool = False
    no_weekly_rotate: bool = False
    daily_rs: bool = False
    alt_trail_pct: float = 0.0
    alt_stop_pct: float = 0.0
    alt_tp_pct: float = 0.0
    alt_max_days: int = 0
    alt_min_excess: float | None = None
    alt_atr_k: float = 0.0
    alt_donch_n: int = 0
    alt_partial_tp_pct: float = 0.0
    alt_partial_frac: float = 0.0
    fold_alt_to_btc: bool = False


def POLICIES() -> list[ExitPolicy]:
    """Scan grid. Live clip is ``live`` (SMA50 flatten + weekly RS, no trail)."""
    rows = [
        ExitPolicy(name="live"),
        ExitPolicy(name="btc_only", alt_frac=0.0),
        ExitPolicy(name="sma20", sma_n=20),
        ExitPolicy(name="sma100", sma_n=100),
        ExitPolicy(name="hold_through_sma", ignore_sma_flatten=True),
        ExitPolicy(name="no_weekly_rotate", no_weekly_rotate=True),
        ExitPolicy(name="daily_rs", daily_rs=True),
        ExitPolicy(name="alt_excess_lte_0", alt_min_excess=0.0),
        ExitPolicy(name="alt_excess_lt_floor", alt_min_excess=0.08),
        ExitPolicy(name="alt_time_7d", alt_max_days=7),
        ExitPolicy(name="alt_time_14d", alt_max_days=14),
        ExitPolicy(name="alt_time_21d", alt_max_days=21),
        ExitPolicy(name="alt_donch_10", alt_donch_n=10),
        ExitPolicy(name="alt_fold_to_btc_trail12", alt_trail_pct=0.12, fold_alt_to_btc=True),
    ]
    for pct in (0.08, 0.10, 0.12, 0.15, 0.20, 0.25):
        tag = int(round(pct * 100))
        rows.append(ExitPolicy(name=f"alt_trail_{tag}", alt_trail_pct=pct))
    for pct in (0.08, 0.12, 0.15, 0.20):
        tag = int(round(pct * 100))
        rows.append(ExitPolicy(name=f"alt_stop_{tag}", alt_stop_pct=pct))
    for pct in (0.10, 0.15, 0.20, 0.30):
        tag = int(round(pct * 100))
        rows.append(ExitPolicy(name=f"alt_tp_{tag}", alt_tp_pct=pct))
    rows.extend(
        [
            ExitPolicy(name="alt_tp20_trail12", alt_tp_pct=0.20, alt_trail_pct=0.12),
            ExitPolicy(name="alt_stop12_trail15", alt_stop_pct=0.12, alt_trail_pct=0.15),
            ExitPolicy(name="alt_partial_tp15", alt_partial_tp_pct=0.15, alt_partial_frac=0.5),
            ExitPolicy(name="alt_atr_2x", alt_atr_k=2.0),
            ExitPolicy(name="alt_atr_3x", alt_atr_k=3.0),
        ]
    )
    return rows
