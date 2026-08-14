"""Global multi-market composite: desk + funding + FX + equity strategies."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from bot.core.config import Settings
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.strategies.base import BaseStrategy


class GlobalCompositeStrategy(BaseStrategy):
    """Runs all enabled strategy modules and defers ranking to GlobalOpportunityEngine."""

    name = "global_composite"

    def __init__(self, settings: Settings, *, children: list[BaseStrategy]) -> None:
        super().__init__()
        self._settings = settings
        self._children = children
        self._max_raw = int(getattr(settings, "opportunity_max_candidates_per_cycle", 20) or 20)

    def update_adverse_bps(self, adverse_bps: Decimal) -> None:
        for child in self._children:
            if hasattr(child, "update_adverse_bps"):
                child.update_adverse_bps(adverse_bps)  # type: ignore[attr-defined]

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        return []

    async def evaluate_markets(
        self,
        snapshots: Sequence[MarketSnapshot],
        *,
        equity: Decimal | None = None,
        inventory: object = None,
    ) -> list[TradeOpportunity]:
        kwargs: dict = {"equity": equity, "inventory": inventory}
        combined: list[TradeOpportunity] = []
        for child in self._children:
            evaluate = getattr(child, "evaluate_markets", None)
            if not callable(evaluate):
                continue
            try:
                opps = await evaluate(snapshots, **kwargs)
            except TypeError:
                opps = await evaluate(snapshots, equity=equity)
            combined.extend(opps)
        return combined[: self._max_raw]

    def scan_stats(self) -> dict[str, object]:
        reject: dict[str, int] = {}
        pairs = emits = rejects = edges = 0
        child_stats: dict[str, object] = {}
        for child in self._children:
            if not hasattr(child, "scan_stats"):
                continue
            stats = child.scan_stats()  # type: ignore[attr-defined]
            name = getattr(child, "name", child.__class__.__name__)
            child_stats[name] = stats
            pairs += int(stats.get("pairs_evaluated", 0) or 0)
            emits += int(stats.get("opportunities_emitted", 0) or 0)
            rejects += int(stats.get("scan_rejections", 0) or 0)
            edges += int(stats.get("depth_edges_found", 0) or 0)
            for k, v in (stats.get("reject_counts") or {}).items():
                reject[str(k)] = reject.get(str(k), 0) + int(v)
        return {
            "pairs_evaluated": pairs,
            "depth_edges_found": edges,
            "scan_rejections": rejects,
            "opportunities_emitted": emits,
            "reject_counts": dict(sorted(reject.items())),
            "children": child_stats,
        }
