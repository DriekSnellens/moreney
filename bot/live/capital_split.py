"""Core + satellite capital allocation for the live micro desk.

Core (default ~65%): cash reserve, optionally light BTC/ETH long-hold.
Satellite (~35%): trend/momentum recycle sleeve with hard cut-loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapitalSplitPlan:
    enabled: bool
    budget_eur: float
    core_fraction: float
    satellite_fraction: float
    core_mode: str
    core_eur: float
    satellite_eur: float
    ring_per_venue_eur: float
    sleeve_daily_loss_cap_eur: float
    cut_loss_below_be_pct: float
    early_cut_loss_below_be_pct: float
    long_hold_bases: str
    max_alt_inventory_pct: float
    n_venues: int

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "budget_eur": round(self.budget_eur, 2),
            "core_fraction": self.core_fraction,
            "satellite_fraction": self.satellite_fraction,
            "core_mode": self.core_mode,
            "core_eur": round(self.core_eur, 2),
            "satellite_eur": round(self.satellite_eur, 2),
            "ring_per_venue_eur": round(self.ring_per_venue_eur, 2),
            "sleeve_daily_loss_cap_eur": round(self.sleeve_daily_loss_cap_eur, 2),
            "cut_loss_below_be_pct": self.cut_loss_below_be_pct,
            "early_cut_loss_below_be_pct": self.early_cut_loss_below_be_pct,
            "long_hold_bases": self.long_hold_bases,
            "max_alt_inventory_pct": self.max_alt_inventory_pct,
            "n_venues": self.n_venues,
        }


def resolve_capital_split(
    budget_eur: float,
    *,
    n_venues: int = 1,
    enabled: bool = True,
    core_fraction: float = 0.65,
    satellite_fraction: float | None = None,
    core_mode: str = "cash",
    cut_loss_below_be_pct: float = 0.025,
    early_cut_loss_below_be_pct: float = 0.01,
    core_long_hold_bases: str = "BTC,ETH",
    sleeve_daily_loss_cap_eur: float | None = None,
) -> CapitalSplitPlan:
    """Build a deterministic core/satellite plan for a pocket budget."""
    budget = max(float(budget_eur or 0.0), 0.0)
    venues = max(int(n_venues or 1), 1)
    core_frac = min(max(float(core_fraction), 0.0), 0.95)
    if satellite_fraction is None:
        sat_frac = max(0.05, 1.0 - core_frac)
    else:
        sat_frac = min(max(float(satellite_fraction), 0.05), 0.95)
        core_frac = max(0.0, 1.0 - sat_frac)
    mode = str(core_mode or "cash").strip().lower().replace("-", "_")
    if mode in {"btc_eth", "btceth", "hold_btc_eth"}:
        mode = "btc_eth"
    else:
        mode = "cash"

    if not enabled or budget <= 0:
        # Legacy full-pocket velocity desk behaviour.
        ring = min(1850.0, budget * 0.925) if budget > 0 else 0.0
        return CapitalSplitPlan(
            enabled=False,
            budget_eur=budget,
            core_fraction=0.0,
            satellite_fraction=1.0,
            core_mode=mode,
            core_eur=0.0,
            satellite_eur=ring,
            ring_per_venue_eur=ring,
            sleeve_daily_loss_cap_eur=50.0,
            cut_loss_below_be_pct=float(cut_loss_below_be_pct),
            early_cut_loss_below_be_pct=float(early_cut_loss_below_be_pct),
            long_hold_bases="",
            max_alt_inventory_pct=78.0,
            n_venues=venues,
        )

    satellite = budget * sat_frac
    core = budget * core_frac
    ring = satellite / venues
    if sleeve_daily_loss_cap_eur is None or float(sleeve_daily_loss_cap_eur) <= 0:
        # ~3% of satellite working capital (scaled from prior €50 on ~€1850).
        cap = max(12.0, round(satellite * 0.03, 2))
    else:
        cap = float(sleeve_daily_loss_cap_eur)

    long_hold = ""
    if mode == "btc_eth":
        long_hold = ",".join(
            b.strip().upper()
            for b in str(core_long_hold_bases or "BTC,ETH").split(",")
            if b.strip()
        )

    return CapitalSplitPlan(
        enabled=True,
        budget_eur=budget,
        core_fraction=core_frac,
        satellite_fraction=sat_frac,
        core_mode=mode,
        core_eur=core,
        satellite_eur=satellite,
        ring_per_venue_eur=ring,
        sleeve_daily_loss_cap_eur=cap,
        cut_loss_below_be_pct=float(cut_loss_below_be_pct),
        early_cut_loss_below_be_pct=float(early_cut_loss_below_be_pct),
        long_hold_bases=long_hold,
        max_alt_inventory_pct=round(sat_frac * 100.0, 1),
        n_venues=venues,
    )


def split_session_overrides(plan: CapitalSplitPlan) -> dict[str, Any]:
    """Settings keys applied by the live micro session when split is active."""
    if not plan.enabled:
        return {}
    # Slightly smaller clips so a €700 satellite is not one or two bags.
    first_clip = min(120.0, max(55.0, plan.satellite_eur * 0.15))
    add_clip = min(160.0, max(55.0, plan.satellite_eur * 0.20))
    return {
        "live_micro_capital_split_enabled": True,
        "live_micro_core_fraction": plan.core_fraction,
        "live_micro_satellite_fraction": plan.satellite_fraction,
        "live_micro_core_mode": plan.core_mode,
        "live_micro_core_eur": plan.core_eur,
        "live_micro_satellite_eur": plan.satellite_eur,
        "live_micro_active_ring_eur": plan.ring_per_venue_eur,
        "live_micro_velocity_sleeve_eur": plan.satellite_eur,
        "live_micro_velocity_sleeve_daily_loss_cap_eur": plan.sleeve_daily_loss_cap_eur,
        "live_micro_ring_soft_max_active_eur": plan.ring_per_venue_eur,
        "live_micro_long_hold_bases": plan.long_hold_bases,
        "live_micro_cut_loss_below_be_pct": plan.cut_loss_below_be_pct,
        "live_micro_cut_loss_new_bases_only": False,
        "live_micro_early_cut_loss_below_be_pct": plan.early_cut_loss_below_be_pct,
        "live_micro_early_cut_new_bases_only": True,
        "paper_max_alt_inventory_pct": plan.max_alt_inventory_pct,
        "live_micro_first_clip_eur": first_clip,
        "live_micro_add_clip_eur": add_clip,
        "live_micro_alphai_priority_clip_eur": min(180.0, add_clip * 1.25),
        "live_micro_alphai_strong_clip_eur": min(220.0, add_clip * 1.5),
        "live_micro_okx_ring_clip_eur": min(100.0, first_clip),
    }
