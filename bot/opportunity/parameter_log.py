"""Reproduceable parameter / behavior changes for this NET-PnL pass.

Each entry is a measured change, not a magic knob. Live fleet env files are
intentionally untouched (paper instances keep running with their current
configs). Code defaults and ranking logic are what changed.
"""

from __future__ import annotations

PARAMETER_CHANGES: list[dict[str, str]] = [
    {
        "file": "bot/opportunity/ranker.py",
        "change": "Rank by calibrated NET EV, then net_eur_per_capital_second",
        "old": "score = raw EV × regime × liquidity × execution_quality",
        "new": "score uses calibrated_EV; velocity is the tie-breaker",
        "reason": "Primary goal is NET euro per bound euro per second, not trade count",
        "expected_effect": "Quotes with high theoretical EV but poor capture rank lower",
        "risk": "Small-sample shrinkage may under-rank a newly good venue",
    },
    {
        "file": "bot/opportunity/ev_engine.py",
        "change": "Stop hardcoding markout win_rate=0.55",
        "old": "If any markout sample exists, p_win = 0.55",
        "new": "Use empirical markout win rate only when n >= min_samples; else default",
        "reason": "€25k paper: expected +€30.80 vs realized −€36.93; 0.55 was fake calibration",
        "expected_effect": "EV closer to realized NET as fills accumulate",
        "risk": "Until min_samples, EV stays at the uninformative prior",
    },
    {
        "file": "bot/strategies/maker_inventory.py",
        "change": "Remove +€0.05 same-venue ranking bonus",
        "old": "same_venue_bonus = 0.05 EUR",
        "new": "0; venue quality comes from calibrated capture / markout",
        "reason": "bitvavo→bitvavo is 14/21 trades and −€29.33 of −€36.93 on €25k",
        "expected_effect": "Fewer same-venue Bitvavo quotes selected when NET is similar",
        "risk": "Genuine same-venue edge on cheaper venues (OKX) no longer gets a bonus",
    },
    {
        "file": "bot/opportunity/portfolio_gate.py",
        "change": "Correlation sync uses registry groups; venue cap counts buy and sell",
        "old": "symbol[:3] vs crypto_btc_beta mismatch; buy venue only",
        "new": "BTC/ETH/alt groups; max(buy, sell) venue notional",
        "reason": "Caps were not measuring the exposure they claimed to cap",
        "expected_effect": "More accurate concentration rejects; not a looser cap",
        "risk": "Slightly more venue rejects if sell-leg concentration was invisible",
    },
    {
        "file": "bot/strategies/triangle_bridge.py",
        "change": "Deduct EURUSDT taker FX cost from NET at detection",
        "old": "FX cost only in fees_eat_edge pre-gate and post-trade refill",
        "new": "extra_cost_eur in profitability NET",
        "reason": "Detected NET and realized triangle PnL diverged",
        "expected_effect": "Fewer false-positive triangle emits",
        "risk": "Slightly fewer triangle quotes",
    },
    {
        "file": "bot/execution/paper_executor.py",
        "change": "Limit orders without a book are not marketable",
        "old": "book is None → fill at requested price",
        "new": "book is None → not marketable",
        "reason": "Optimistic paper fills when the book is missing",
        "expected_effect": "More conservative paper PnL",
        "risk": "Occasional missed arb leg if a snapshot is absent for one cycle",
    },
    {
        "file": "bot/paper/markout.py",
        "change": "Add 60s horizon and venue×symbol×side buckets",
        "old": "1s/5s/30s global median",
        "new": "1s/5s/30s/60s + per-venue suggested adverse",
        "reason": "Bitvavo toxicity was averaged away in a global haircut",
        "expected_effect": "Higher required edge on venues with worse markout, not a hard disable",
        "risk": "Thin per-venue samples shrink toward the global floor",
    },
    {
        "file": "bot/core/config.py",
        "change": "Default scan symbols include ADAUSDT and NEARUSDT",
        "old": "Triangle bases ADA/NEAR had EUR books only",
        "new": "Both quote legs present in the default universe",
        "reason": "Triangle detector never saw ADA/NEAR USDT books (scanned=0 for those bases)",
        "expected_effect": "Triangle can evaluate ADA/NEAR; still gated by NET/fees",
        "risk": "More market-data subscriptions on default (not fleet) configs",
    },
]
