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
    # Once the alt peaks this far above entry, sell if close falls back to
    # entry * (1 + floor). Floor 0 is breakeven. Does nothing until armed.
    alt_lock_arm_pct: float = 0.0
    alt_lock_floor_pct: float = 0.0
    # Shallow spike: while the peak gain is still inside [arm, max), a
    # giveback from that peak sells. Above max the 10% trail is left in charge.
    alt_spike_arm_pct: float = 0.0
    alt_spike_max_pct: float = 0.0
    alt_spike_giveback_pct: float = 0.0
    alt_spike_frac: float = 1.0
    # Only the session that prints the high. Later drifts stay on the trail.
    alt_spike_same_day: bool = False
    # After a full spike sell, buy the sleeve back if a later close reclaims
    # that day's high and the name is still the residual winner. 0 disables.
    # The new lot is left to the trail.
    alt_spike_reclaim_days: int = 0
    fold_alt_to_btc: bool = False
    # After the peak is this far above entry, the trail width becomes
    # ``alt_runner_trail_pct``. 0 leaves the ordinary trail in charge.
    # The lock still uses its own arm. This is not a globally wider trail.
    alt_runner_arm_pct: float = 0.0
    alt_runner_trail_pct: float = 0.0
    # Weekdays (0=Mon … 6=Sun) on which the price trail may fire.
    # Empty means every completed close. The lock is not gated by this.
    alt_trail_weekdays: tuple[int, ...] = ()
    # Consecutive closes through the trail before a full sell. 1 is the
    # live pack: the first close through the level sells. The lock is not
    # counted here and still sells on one close.
    alt_trail_confirm: int = 1
    # On the first close through the trail, sell this fraction and keep
    # the rest. 0 sells nothing until ``alt_trail_confirm`` is reached.
    # Only applies when confirm is at least 2. The sold half is not refilled.
    alt_trail_first_frac: float = 0.0
    # Fill the trail from the prior peak when today's low trades through it.
    # The level is known before the bar. A gap through it fills at the open.
    # The close rule still runs when the low does not reach that level.
    alt_trail_on_touch: bool = False
    # Same fill for the lock floor, armed off the prior peak. The close
    # lock still runs when this is false.
    alt_lock_on_touch: bool = False
    # Sell a full lot that is still under its entry after this many days.
    # 0 leaves the live trail and lock in charge. A close above the entry
    # does not arm this, so a winner is not cut for being old.
    alt_red_cut_days: int = 0
    # When set, that red cut is dropped if the name is still the top
    # residual qualifier. A red leader stays on the live trail. Other
    # reasons on the same sell, such as the trail itself, still fire.
    alt_red_cut_if_leader: bool = False


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
