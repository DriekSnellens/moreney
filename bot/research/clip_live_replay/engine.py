"""Causal wet replay of the armed BTC+RS clip pack."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.live.momentum_btc_rs_clip import ClipConfig
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET, FillModel
from bot.research.clip_exit_lab.policies import ExitPolicy


def live_pack_knobs(cfg: ClipConfig | None = None) -> dict[str, Any]:
    """Map live ClipConfig onto the residual-weekly wet engine."""
    c = cfg or ClipConfig()
    trail = float(c.alt_trail_pct or 0.0)
    return {
        "btc_frac": float(c.btc_frac),
        "excess_floor": float(c.excess_floor),
        "flatten": "all",
        "sma_n": int(c.sma_n),
        "rebalance_days": int(c.rebalance_days),
        "lookback_days": int(c.lookback_days),
        "skip_days": int(c.skip_days),
        "n_alts": 1,
        "require_alt_sma": False,
        "trail_pct": trail,
        "policy": (
            ExitPolicy(name=f"alt_trail_{int(round(trail * 100))}", alt_trail_pct=trail)
            if trail > 0
            else None
        ),
    }


def run_live_pack(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel = WET,
    cfg: ClipConfig | None = None,
    alt_allow: Mapping[str, set[str]] | None = None,
    alt_allow_mode: str = "gate",
    cash_when_no_alt: bool = False,
) -> dict[str, Any]:
    knobs = live_pack_knobs(cfg)
    policy = knobs.pop("policy")
    trail_pct = knobs.pop("trail_pct")
    row = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book_eur,
        model=model,
        policy=policy,
        keep_curve=True,
        keep_weeks=True,
        keep_trades=True,
        strategy="live_clip_pack",
        alt_allow=alt_allow,
        alt_allow_mode=alt_allow_mode,
        cash_when_no_alt=cash_when_no_alt,
        **knobs,
    )
    row["trail_pct"] = trail_pct
    row["pack"] = {
        **knobs,
        "trail_pct": trail_pct,
        "exit_policy": None if policy is None else policy.name,
    }
    return row
