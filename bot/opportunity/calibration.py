"""Historical EV calibration with shrinkage.

Maps raw expected NET → calibrated expected NET using completed paper
fills only (no look-ahead). Small samples shrink toward an uninformative
prior of 1.0 (trust the model until evidence accumulates).

A bucket with theoretically high EV but structurally bad realized PnL
becomes less attractive automatically. Negative capture is only used as a
hard gate once ``min_samples`` is reached.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from bot.core.models import TradeOpportunity

_ZERO = Decimal("0")
_ONE = Decimal("1")


def calibration_key(opportunity: TradeOpportunity) -> str:
    meta = opportunity.metadata or {}
    buy = str(meta.get("buy_exchange") or "").strip().lower()
    sell = str(meta.get("sell_exchange") or "").strip().lower()
    side = opportunity.side.value if hasattr(opportunity.side, "value") else str(opportunity.side)
    return (
        f"{opportunity.strategy_name}|{opportunity.symbol.upper()}|"
        f"{buy}->{sell}|{side}"
    )


def route_key(opportunity: TradeOpportunity) -> str:
    meta = opportunity.metadata or {}
    buy = str(meta.get("buy_exchange") or "").strip().lower()
    sell = str(meta.get("sell_exchange") or "").strip().lower()
    return f"{buy}->{sell}"


class _Bucket:
    __slots__ = ("n", "sum_expected", "sum_realized")

    def __init__(self) -> None:
        self.n = 0
        self.sum_expected = _ZERO
        self.sum_realized = _ZERO

    def observe(self, expected: Decimal, realized: Decimal) -> None:
        self.n += 1
        self.sum_expected += expected
        self.sum_realized += realized

    def raw_capture(self) -> Decimal | None:
        if self.n <= 0:
            return None
        if abs(self.sum_expected) < Decimal("0.01"):
            return None
        return self.sum_realized / self.sum_expected


class EvCalibrator:
    """James-Stein style shrinkage of EV capture ratios."""

    def __init__(
        self,
        *,
        prior_strength: int = 40,
        min_samples: int = 20,
        prior_capture: Decimal = _ONE,
        capture_floor: Decimal = Decimal("-0.5"),
        capture_ceiling: Decimal = Decimal("1.5"),
    ) -> None:
        self._prior_k = max(1, prior_strength)
        self._min_samples = max(1, min_samples)
        self._prior = prior_capture
        self._floor = capture_floor
        self._ceiling = capture_ceiling
        self._by_key: dict[str, _Bucket] = defaultdict(_Bucket)
        self._by_route: dict[str, _Bucket] = defaultdict(_Bucket)
        self._by_strategy: dict[str, _Bucket] = defaultdict(_Bucket)
        self._global = _Bucket()

    def observe(
        self,
        *,
        key: str,
        route: str,
        strategy: str,
        expected_net: Decimal,
        realized_net: Decimal,
    ) -> None:
        self._by_key[key].observe(expected_net, realized_net)
        if route:
            self._by_route[route].observe(expected_net, realized_net)
        if strategy:
            self._by_strategy[strategy].observe(expected_net, realized_net)
        self._global.observe(expected_net, realized_net)

    def capture_ratio(
        self,
        *,
        key: str,
        route: str = "",
        strategy: str = "",
    ) -> Decimal:
        """Shrunk capture ratio. 1.0 = raw EV is unbiased."""
        parts: list[tuple[int, Decimal | None]] = [
            (self._by_key[key].n, self._by_key[key].raw_capture()),
            (self._by_route[route].n if route else 0, self._by_route[route].raw_capture() if route else None),
            (
                self._by_strategy[strategy].n if strategy else 0,
                self._by_strategy[strategy].raw_capture() if strategy else None,
            ),
            (self._global.n, self._global.raw_capture()),
        ]
        # Prefer the most specific bucket with any samples; still shrink hard.
        n = 0
        raw: Decimal | None = None
        for count, capture in parts:
            if count > 0 and capture is not None:
                n = count
                raw = capture
                break
        if raw is None:
            return self._prior
        alpha = Decimal(n) / Decimal(n + self._prior_k)
        shrunk = alpha * raw + (_ONE - alpha) * self._prior
        return min(self._ceiling, max(self._floor, shrunk))

    def calibrate(
        self,
        raw_ev: Decimal,
        opportunity: TradeOpportunity,
    ) -> Decimal:
        key = calibration_key(opportunity)
        ratio = self.capture_ratio(
            key=key,
            route=route_key(opportunity),
            strategy=opportunity.strategy_name,
        )
        return raw_ev * ratio

    def hard_gate_negative(self, opportunity: TradeOpportunity) -> bool:
        """True when sample size is enough to treat negative capture as a reject."""
        bucket = self._by_key[calibration_key(opportunity)]
        if bucket.n < self._min_samples:
            route = self._by_route[route_key(opportunity)]
            if route.n < self._min_samples:
                return False
            cap = self.capture_ratio(
                key=calibration_key(opportunity),
                route=route_key(opportunity),
                strategy=opportunity.strategy_name,
            )
            return cap <= 0
        cap = self.capture_ratio(
            key=calibration_key(opportunity),
            route=route_key(opportunity),
            strategy=opportunity.strategy_name,
        )
        return cap <= 0

    def snapshot(self) -> dict[str, Any]:
        def _summ(bucket: _Bucket) -> dict[str, Any]:
            return {
                "n": bucket.n,
                "sum_expected": str(bucket.sum_expected),
                "sum_realized": str(bucket.sum_realized),
                "raw_capture": str(bucket.raw_capture()) if bucket.raw_capture() is not None else None,
            }

        routes = {
            k: _summ(v)
            for k, v in sorted(self._by_route.items(), key=lambda kv: -kv[1].n)
            if v.n > 0
        }
        return {
            "global": _summ(self._global),
            "min_samples": self._min_samples,
            "prior_strength": self._prior_k,
            "prior_capture": str(self._prior),
            "routes": routes,
            "strategies": {k: _summ(v) for k, v in self._by_strategy.items() if v.n > 0},
            "keys": len([1 for v in self._by_key.values() if v.n > 0]),
        }

    def export_state(self) -> dict[str, Any]:
        def _dump(store: dict[str, _Bucket]) -> dict[str, dict[str, str]]:
            return {
                k: {
                    "n": str(v.n),
                    "sum_expected": str(v.sum_expected),
                    "sum_realized": str(v.sum_realized),
                }
                for k, v in store.items()
                if v.n > 0
            }

        return {
            "keys": _dump(self._by_key),
            "routes": _dump(self._by_route),
            "strategies": _dump(self._by_strategy),
            "global": {
                "n": str(self._global.n),
                "sum_expected": str(self._global.sum_expected),
                "sum_realized": str(self._global.sum_realized),
            },
        }

    def import_state(self, data: dict[str, Any] | None) -> None:
        if not data:
            return

        def _load(raw: dict[str, Any] | None, store: dict[str, _Bucket]) -> None:
            if not isinstance(raw, dict):
                return
            for key, payload in raw.items():
                if not isinstance(payload, dict):
                    continue
                bucket = store[str(key)]
                bucket.n = int(payload.get("n", 0) or 0)
                bucket.sum_expected = Decimal(str(payload.get("sum_expected", "0")))
                bucket.sum_realized = Decimal(str(payload.get("sum_realized", "0")))

        _load(data.get("keys"), self._by_key)
        _load(data.get("routes"), self._by_route)
        _load(data.get("strategies"), self._by_strategy)
        g = data.get("global") or {}
        if isinstance(g, dict):
            self._global.n = int(g.get("n", 0) or 0)
            self._global.sum_expected = Decimal(str(g.get("sum_expected", "0")))
            self._global.sum_realized = Decimal(str(g.get("sum_realized", "0")))
