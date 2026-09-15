"""Interpretable toxicity estimators with hierarchical shrinkage.

Models:
  A. Global prior
  B. Route prior
  C. Hierarchical: global → venue → route → symbol|side
  D. Bucketed: quote_age × vol × spread × side (with fallback)

Positive adverse_bps = market moved against the fill (same sign as MarkoutTracker).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from math import sqrt
from typing import Any, Iterable

from bot.opportunity.toxicity.types import PreTradeFeatures, ToxicityPrediction

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _mean(vals: list[Decimal]) -> Decimal:
    if not vals:
        return _ZERO
    return sum(vals, _ZERO) / Decimal(len(vals))


def _std(vals: list[Decimal]) -> Decimal:
    if len(vals) < 2:
        return _ZERO
    m = _mean(vals)
    var = sum((x - m) ** 2 for x in vals) / Decimal(len(vals) - 1)
    return Decimal(str(sqrt(float(var))))


@dataclass
class _Cell:
    values: list[Decimal] = field(default_factory=list)

    def observe(self, adverse_bps: Decimal) -> None:
        self.values.append(adverse_bps)

    @property
    def n(self) -> int:
        return len(self.values)

    def mean(self) -> Decimal:
        return _mean(self.values)

    def std(self) -> Decimal:
        return _std(self.values)


class HierarchicalToxicityModel:
    """Empirical-Bayes style hierarchical shrinkage for adverse bps."""

    def __init__(
        self,
        *,
        prior_strength: int = 8,
        model: str = "C_HIERARCHICAL",
        global_prior_bps: Decimal | None = None,
    ) -> None:
        self.prior_strength = max(1, int(prior_strength))
        self.model = model
        self._forced_prior = global_prior_bps
        self.global_cell = _Cell()
        self.by_venue: dict[str, _Cell] = defaultdict(_Cell)
        self.by_route: dict[str, _Cell] = defaultdict(_Cell)
        self.by_symbol_side: dict[str, _Cell] = defaultdict(_Cell)
        self.by_bucket: dict[str, _Cell] = defaultdict(_Cell)
        self._all_observed: list[Decimal] = []

    def observe(self, features: PreTradeFeatures, adverse_bps: Decimal) -> None:
        """Learn from a completed fill only (never from rejects)."""
        adv = _d(adverse_bps)
        self.global_cell.observe(adv)
        self._all_observed.append(adv)
        venue = (features.venue or "").lower()
        route = (features.route or "").lower()
        sym_side = f"{(features.symbol or '').upper()}|{(features.side or '').lower()}"
        self.by_venue[venue].observe(adv)
        self.by_route[route].observe(adv)
        self.by_symbol_side[sym_side].observe(adv)
        self.by_bucket[self._bucket_key(features)].observe(adv)

    def predict(self, features: PreTradeFeatures) -> ToxicityPrediction:
        if self.model == "A_GLOBAL":
            return self._predict_global(features)
        if self.model == "B_ROUTE":
            return self._predict_route(features)
        if self.model == "D_BUCKETED":
            return self._predict_bucketed(features)
        return self._predict_hierarchical(features)

    def _global_prior(self) -> Decimal:
        if self._forced_prior is not None:
            return _d(self._forced_prior)
        if self.global_cell.n > 0:
            return self.global_cell.mean()
        # Uninformative prior ≈ current adverse gate (~15 bps), not an OOS-tuned fit.
        return Decimal("15")

    def _shrink(
        self, local: Decimal, n: int, prior: Decimal, *, k: int | None = None
    ) -> Decimal:
        strength = self.prior_strength if k is None else k
        w = Decimal(n) / Decimal(n + strength)
        return w * local + (Decimal("1") - w) * prior

    def _uncertainty(self, n: int, cell_std: Decimal) -> Decimal:
        # Sparse cells → larger uncertainty; never claim precision at n=0.
        base = cell_std if cell_std > 0 else Decimal("12")
        if n <= 0:
            return Decimal("25")
        return base / Decimal(str(sqrt(n))) + Decimal(str(max(0, 6 - n)))

    def _eur(self, bps: Decimal, features: PreTradeFeatures) -> Decimal:
        notional = features.notional_eur
        if notional <= 0:
            return _ZERO
        return bps / _BPS * notional

    def _percentile(self, bps: Decimal) -> Decimal | None:
        if not self._all_observed:
            return None
        below = sum(1 for x in self._all_observed if x <= bps)
        return Decimal(below) / Decimal(len(self._all_observed))

    def _predict_global(self, features: PreTradeFeatures) -> ToxicityPrediction:
        bps = self._global_prior()
        n = self.global_cell.n
        unc = self._uncertainty(n, self.global_cell.std())
        return ToxicityPrediction(
            expected_adverse_bps=bps,
            expected_adverse_eur=self._eur(bps, features),
            sample_count=n,
            uncertainty_bps=unc,
            shrinkage_source="global",
            toxicity_percentile=self._percentile(bps),
            model_name="A_GLOBAL",
        )

    def _predict_route(self, features: PreTradeFeatures) -> ToxicityPrediction:
        prior = self._global_prior()
        route = (features.route or "").lower()
        cell = self.by_route.get(route) or _Cell()
        if cell.n <= 0:
            bps = prior
            src = "global(fallback)"
        else:
            bps = self._shrink(cell.mean(), cell.n, prior)
            src = f"route:{route}"
        unc = self._uncertainty(cell.n, cell.std() if cell.n else self.global_cell.std())
        return ToxicityPrediction(
            expected_adverse_bps=bps,
            expected_adverse_eur=self._eur(bps, features),
            sample_count=cell.n,
            uncertainty_bps=unc,
            shrinkage_source=src,
            toxicity_percentile=self._percentile(bps),
            model_name="B_ROUTE",
        )

    def _predict_hierarchical(self, features: PreTradeFeatures) -> ToxicityPrediction:
        """global → venue → route → symbol|side."""
        g = self._global_prior()
        venue = (features.venue or "").lower()
        route = (features.route or "").lower()
        sym_side = f"{(features.symbol or '').upper()}|{(features.side or '').lower()}"
        v_cell = self.by_venue.get(venue) or _Cell()
        r_cell = self.by_route.get(route) or _Cell()
        s_cell = self.by_symbol_side.get(sym_side) or _Cell()

        level = g
        src_parts = ["global"]
        if v_cell.n > 0:
            level = self._shrink(v_cell.mean(), v_cell.n, level)
            src_parts.append(f"venue:{venue}(n={v_cell.n})")
        if r_cell.n > 0:
            level = self._shrink(r_cell.mean(), r_cell.n, level)
            src_parts.append(f"route:{route}(n={r_cell.n})")
        if s_cell.n > 0:
            level = self._shrink(s_cell.mean(), s_cell.n, level, k=max(4, self.prior_strength // 2))
            src_parts.append(f"symbol_side:{sym_side}(n={s_cell.n})")

        n_eff = s_cell.n or r_cell.n or v_cell.n or self.global_cell.n
        cell_std = (
            s_cell.std()
            if s_cell.n >= 2
            else (r_cell.std() if r_cell.n >= 2 else self.global_cell.std())
        )
        unc = self._uncertainty(n_eff, cell_std)
        return ToxicityPrediction(
            expected_adverse_bps=level,
            expected_adverse_eur=self._eur(level, features),
            sample_count=n_eff,
            uncertainty_bps=unc,
            shrinkage_source="→".join(src_parts),
            toxicity_percentile=self._percentile(level),
            model_name="C_HIERARCHICAL",
            notes=("hierarchical_shrinkage",),
        )

    def _bucket_key(self, features: PreTradeFeatures) -> str:
        return (
            f"{features.quote_age_bucket}|{features.vol_bucket}|"
            f"{features.spread_bucket}|{(features.side or '').lower()}"
        )

    def _predict_bucketed(self, features: PreTradeFeatures) -> ToxicityPrediction:
        prior = self._global_prior()
        key = self._bucket_key(features)
        cell = self.by_bucket.get(key) or _Cell()
        # Fallback: drop vol, then age, then side-only via hierarchical.
        if cell.n <= 0:
            # try age × side
            alt = f"{features.quote_age_bucket}|unknown|{features.spread_bucket}|{(features.side or '').lower()}"
            cell = self.by_bucket.get(alt) or _Cell()
        if cell.n <= 0:
            return self._predict_hierarchical(features)
        bps = self._shrink(cell.mean(), cell.n, prior)
        unc = self._uncertainty(cell.n, cell.std())
        return ToxicityPrediction(
            expected_adverse_bps=bps,
            expected_adverse_eur=self._eur(bps, features),
            sample_count=cell.n,
            uncertainty_bps=unc,
            shrinkage_source=f"bucket:{key}",
            toxicity_percentile=self._percentile(bps),
            model_name="D_BUCKETED",
        )

    def export_state(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prior_strength": self.prior_strength,
            "global_n": self.global_cell.n,
            "global_mean_bps": str(self.global_cell.mean()) if self.global_cell.n else None,
            "routes": {k: {"n": c.n, "mean": str(c.mean())} for k, c in self.by_route.items()},
            "venues": {k: {"n": c.n, "mean": str(c.mean())} for k, c in self.by_venue.items()},
        }


def fit_models(
    events: Iterable[tuple[PreTradeFeatures, Decimal]],
    *,
    prior_strength: int = 8,
) -> dict[str, HierarchicalToxicityModel]:
    """Fit A–D on the same observation stream (for offline comparison only)."""
    names = ("A_GLOBAL", "B_ROUTE", "C_HIERARCHICAL", "D_BUCKETED")
    models = {
        name: HierarchicalToxicityModel(prior_strength=prior_strength, model=name)
        for name in names
    }
    for feats, adv in events:
        for m in models.values():
            m.observe(feats, adv)
    return models
