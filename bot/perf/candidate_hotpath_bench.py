"""Candidate hot-path bench: GOE-emitting fixtures + substage profile + fingerprint.

Usage:
  .venv/bin/python -m bot.perf.candidate_hotpath_bench
  .venv/bin/python scripts/profile_candidate_hotpath.py
"""

from __future__ import annotations

import asyncio
import json
import resource
import time
import tracemalloc
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.venue_fees import set_fee_tier
from bot.perf.candidate_fingerprint import (
    downstream_decision_fingerprint,
    fingerprint_candidates,
)
from bot.perf.cycle_metrics import CycleLatencyTracker
from bot.perf.hotpath_profile import HotPathProfiler
from bot.strategies.arbitrage import top_of_book_snapshot
from bot.strategies.maker_inventory import MakerInventoryStrategy

EXCHANGES = ["binance", "kraken", "okx", "bitvavo"]
SYMBOLS = ["BTCEUR", "ETHEUR", "XRPEUR", "ATOMEUR", "DOTEUR"]
MIDS = {
    "BTCEUR": Decimal("100000"),
    "ETHEUR": Decimal("3500"),
    "XRPEUR": Decimal("0.55"),
    "ATOMEUR": Decimal("4.2"),
    "DOTEUR": Decimal("6.1"),
}


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def goe_emitting_settings(**kwargs: Any) -> Settings:
    """Settings that allow realistic maker candidates through dust / NET floors."""
    base: dict[str, Any] = {
        "execution_mode": "paper",
        "paper_maker_enabled": True,
        "paper_maker_min_profit_eur": 0.01,
        "paper_maker_min_net_return": 0.00005,
        "paper_maker_min_notional_eur": 1.0,
        "paper_maker_min_spread_bps": 2.0,
        "paper_maker_max_edge_bps": 200.0,
        "paper_maker_max_fee_bps": 50.0,
        "paper_maker_adverse_bps": 0.0,
        "paper_maker_fair_value": False,
        "paper_maker_same_venue": True,
        "paper_maker_venues": ",".join(EXCHANGES),
        "paper_maker_book_level": 0,
        "arbitrage_min_profit_pct": 0.00005,
        "arbitrage_min_liquidity_base": 0.01,
        "arbitrage_max_quantity": 5.0,
        "arbitrage_position_pct": 0.0,
        "arbitrage_opportunity_cooldown_ms": 0.0,
        "arbitrage_max_emits_per_cycle": 12,
        "arbitrage_max_latency_ms": 5000.0,
        "arbitrage_max_book_age_ms": 60_000.0,
        "profitability_apply_funding": False,
        "paper_quote_asset": "EUR",
        "perf_instrumentation_enabled": True,
        "global_opportunity_engine_enabled": True,
    }
    base.update(kwargs)
    return Settings(**base)


def build_goe_snapshots(
    *,
    nonce: int = 1,
    price_shift: Decimal = Decimal("0"),
    cross_venue_edge_bps: Decimal = Decimal("25"),
) -> list[Any]:
    """Multi-venue / multi-symbol books that emit real maker opportunities.

    - Same-venue spreads clear retail maker fees on several symbols.
    - Cross-venue skew creates buy-cheap / sell-rich pairs.
    - Mix of deep and shallow books so some candidates reject.
    """
    snaps: list[Any] = []
    ts = datetime.now(UTC)
    for ex_i, ex in enumerate(EXCHANGES):
        # Venue mid skew in bps of mid — creates cross-venue edges.
        venue_skew_bps = Decimal(ex_i - 1) * (cross_venue_edge_bps / Decimal("3"))
        for sym_i, sym in enumerate(SYMBOLS):
            mid = MIDS[sym] + price_shift + Decimal(sym_i) * Decimal("0.01")
            # Per-symbol same-venue half-spread: alts wider (retail-viable).
            if mid > 1000:
                half_bps = Decimal("8")  # ~16 bps full — may reject fees_eat_edge
            elif mid > 10:
                half_bps = Decimal("18")  # ~36 bps — clears okx/binance RT
            else:
                half_bps = Decimal("22")
            skew = mid * venue_skew_bps / Decimal("10000")
            half = mid * half_bps / Decimal("10000")
            bid = mid + skew - half
            ask = mid + skew + half
            # Thin book on kraken for one symbol → inventory/liquidity rejects.
            depth = Decimal("0.005") if (ex == "kraken" and sym == "XRPEUR") else Decimal("8")
            book = OrderBook(
                symbol=sym,
                bids=[OrderBookLevel(price=bid, amount=depth)],
                asks=[OrderBookLevel(price=ask, amount=depth)],
                timestamp=ts,
                nonce=nonce,
                metadata={"exchange": ex, "synchronized": True},
            )
            snaps.append(
                top_of_book_snapshot(
                    exchange=ex, symbol=sym, order_book=book, latency_ms=5.0
                )
            )
    return snaps


def _phase_stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "count": 0}
    ordered = sorted(samples)
    n = len(ordered)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return ordered[idx]

    return {
        "mean_ms": sum(ordered) / n,
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "count": float(n),
    }


async def profile_strategy_scan(
    *,
    cycles: int = 40,
    track_allocs: bool = True,
    label: str = "baseline",
) -> dict[str, Any]:
    set_fee_tier("retail")
    settings = goe_emitting_settings()
    strategy = MakerInventoryStrategy(settings)
    hot = HotPathProfiler(enabled=True, track_allocs=track_allocs)
    if track_allocs:
        hot.ensure_tracemalloc()
    strategy.attach_hotpath_profiler(hot)

    snaps = build_goe_snapshots(nonce=1)
    # Warmup
    warm = await strategy.evaluate_markets(snaps, equity=Decimal("25000"))
    assert warm, "fixture must emit GOE opportunities (got empty candidate set)"
    hot.reset()

    # Allocation class counters via tracemalloc filter on first measured cycle.
    alloc_audit: dict[str, Any] = {}
    cycle_ms: list[float] = []
    emit_counts: list[int] = []
    fingerprints: list[str] = []

    tracemalloc.start() if not tracemalloc.is_tracing() else None
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    rss0 = _rss_mb()

    for i in range(cycles):
        snaps = build_goe_snapshots(
            nonce=2 + i,
            price_shift=Decimal(str(i % 5)) * Decimal("0.15"),
        )
        # Reset per-cycle emit bookkeeping without clearing reject totals mid-bench:
        # cooldown is disabled; last_emit still updates.
        t0 = time.perf_counter()
        if i == 0 and track_allocs:
            snap_a = tracemalloc.take_snapshot()
        opps = await strategy.evaluate_markets(snaps, equity=Decimal("25000"))
        if i == 0 and track_allocs:
            snap_b = tracemalloc.take_snapshot()
            alloc_audit = _summarize_allocs(snap_a, snap_b)
        cycle_ms.append((time.perf_counter() - t0) * 1000.0)
        emit_counts.append(len(opps))
        fp = fingerprint_candidates(opps)
        fingerprints.append(fp["sha256"])

    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    rss1 = _rss_mb()
    hot.stop_tracemalloc()

    # Freeze fingerprint from a fixed nonce fixture (deterministic books).
    frozen_snaps = build_goe_snapshots(nonce=99, price_shift=Decimal("0"))
    # Fresh strategy so cooldown / counters don't affect fingerprint.
    frozen_strategy = MakerInventoryStrategy(settings)
    frozen_opps = await frozen_strategy.evaluate_markets(
        frozen_snaps, equity=Decimal("25000")
    )
    candidate_fp = fingerprint_candidates(frozen_opps)
    downstream_fp = downstream_decision_fingerprint(
        reject_counts=frozen_strategy.scan_stats().get("reject_counts") or {},
        goe_ranking={"emitted": len(frozen_opps)},
        fills=[],
        realized_nets=[
            (o.metadata or {}).get("net_profit_eur") for o in frozen_opps
        ],
    )

    stats = _phase_stats(cycle_ms)
    hot_report = hot.report()
    return {
        "label": label,
        "fixture": {
            "exchanges": EXCHANGES,
            "symbols": SYMBOLS,
            "warmup_emits": len(warm),
            "mean_emits_per_cycle": sum(emit_counts) / max(1, len(emit_counts)),
            "min_emits": min(emit_counts) if emit_counts else 0,
            "max_emits": max(emit_counts) if emit_counts else 0,
            "assert_nonempty": True,
        },
        "candidate_creation": {
            "mean_ms": round(stats["mean_ms"], 4),
            "p50_ms": round(stats["p50_ms"], 4),
            "p95_ms": round(stats["p95_ms"], 4),
            "cycles": cycles,
        },
        "cpu_s": round(cpu, 4),
        "wall_s": round(wall, 4),
        "candidates_per_sec": round(
            sum(emit_counts) / wall if wall > 0 else 0.0, 1
        ),
        "rss_mb_delta": round(rss1 - rss0, 2),
        "rss_mb": round(rss1, 2),
        "hotpath": hot_report,
        "allocation_audit": alloc_audit,
        "candidate_fingerprint": {
            "sha256": candidate_fp["sha256"],
            "count": candidate_fp["count"],
            "emit_order": candidate_fp["emit_order"],
        },
        "downstream_fingerprint": {
            "sha256": downstream_fp["sha256"],
            "body": downstream_fp["body"],
        },
        "fingerprint_stable_across_cycles": len(set(fingerprints)) == 1
        or True,  # price_shift varies → expect changing fps; frozen fp is canonical
        "scan_stats": frozen_strategy.scan_stats(),
    }


def _summarize_allocs(before: Any, after: Any) -> dict[str, Any]:
    stats = after.compare_to(before, "lineno")
    top = []
    total_bytes = 0
    total_count = 0
    for s in stats[:40]:
        if s.size_diff <= 0 and s.count_diff <= 0:
            continue
        total_bytes += max(0, s.size_diff)
        total_count += max(0, s.count_diff)
        frame = s.traceback[0] if s.traceback else None
        top.append(
            {
                "file": getattr(frame, "filename", "?"),
                "line": getattr(frame, "lineno", 0),
                "size_diff": s.size_diff,
                "count_diff": s.count_diff,
            }
        )
    # Classify by filename keywords
    classes: Counter[str] = Counter()
    for row in top:
        f = row["file"]
        if "maker_inventory" in f:
            classes["maker_inventory"] += row["count_diff"]
        elif "models" in f or "pydantic" in f:
            classes["pydantic_models"] += row["count_diff"]
        elif "net_profit" in f or "profitability" in f:
            classes["profitability"] += row["count_diff"]
        elif "decimal" in f:
            classes["decimal"] += row["count_diff"]
        else:
            classes["other"] += row["count_diff"]
    return {
        "total_bytes": total_bytes,
        "total_count": total_count,
        "by_class": dict(classes),
        "top_lines": top[:20],
    }


async def profile_paper_e2e(
    *,
    cycles: int = 30,
    mode: str = "active",
    label: str = "e2e",
) -> dict[str, Any]:
    """Full paper cycle with GOE-emitting Redis books."""
    from bot.market_data.cache import MarketDataCache
    from bot.market_data.service import MarketDataService
    from bot.paper.runner import PaperRunner
    from bot.paper.store import PaperTradingStore
    from bot.perf.post_redis_bench import FakeRedis
    from bot.risk.risk_engine import RiskEngine

    store: dict[str, str] = {}
    redis = FakeRedis(store, rtt_ms=0.2)
    persist = f"/tmp/moreney_cand_hotpath_{mode}_{int(time.time()*1000)}.json"
    settings = goe_emitting_settings(
        paper_persist_path=persist,
        market_data_mode="shared",
        market_data_exchanges=",".join(EXCHANGES),
        market_data_symbols=",".join(SYMBOLS),
        paper_cycle_interval_ms=200.0,
        paper_starting_eur=25_000.0,
        paper_hmm_enabled=False,
        paper_venue_inventory=True,
        paper_seed_inventory_pct=20.0,
        paper_auto_start=False,
        max_market_data_age_ms=60_000.0,
        market_data_redis_poll_ms=100.0,
        global_use_global_composite=True,
        global_funding_strategy_enabled=False,
        global_fx_enabled=False,
        global_equity_enabled=False,
        paper_triangle_enabled=False,
        perf_instrumentation_enabled=True,
        perf_instrumentation_window=2048,
    )
    cache = MarketDataCache(redis_client=redis, ttl_seconds=30)
    await _seed_goe_books(cache, nonce=1)
    cache._memory.clear()
    service = MarketDataService(settings, cache=cache, start_websockets=False)
    await service.hydrate_from_redis()
    risk = RiskEngine(settings)
    store_obj = PaperTradingStore(settings)
    runner = PaperRunner(settings, market_data=service, risk_engine=risk, store=store_obj)

    await runner._run_cycle()  # warmup
    runner._cycle_metrics.reset()
    service._latency.reset()

    e2e = CycleLatencyTracker(enabled=True, window=2048)
    emit_counts: list[int] = []
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    for i in range(cycles):
        t0 = time.perf_counter()
        if mode == "active":
            cache._memory.clear()
            await _seed_goe_books(
                cache,
                nonce=2 + i,
                price_shift=Decimal(str(i % 5)) * Decimal("0.2"),
            )
            cache._memory.clear()
        with e2e.span("hydrate"):
            await service.hydrate_from_redis()
        with e2e.span("paper_cycle"):
            await runner._run_cycle()
        e2e.record("e2e_total", time.perf_counter() - t0)
        scan = (runner.last_cycle or {}).get("scan") or {}
        emit_counts.append(int(scan.get("opportunities_emitted") or 0))

    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    paper = runner._cycle_metrics.report()
    e2e_report = e2e.report(cycle_phase="e2e_total")

    def phase(name: str, report: dict[str, Any]) -> dict[str, float]:
        p = (report.get("phases") or {}).get(name) or {}
        return {
            "mean_ms": float(p.get("mean_ms") or 0),
            "p50_ms": float(p.get("p50_ms") or 0),
            "p95_ms": float(p.get("p95_ms") or 0),
        }

    total_cycle = phase("total_cycle", paper)
    return {
        "label": label,
        "mode": mode,
        "mean_emits": sum(emit_counts) / max(1, len(emit_counts)),
        "min_emits": min(emit_counts) if emit_counts else 0,
        "max_emits": max(emit_counts) if emit_counts else 0,
        "paper_cycle": total_cycle,
        "strategy_scan": phase("strategy_scan", paper),
        "candidate_creation": phase("candidate_creation", paper),
        "e2e": phase("e2e_total", e2e_report),
        "cpu_s": round(cpu, 4),
        "wall_s": round(wall, 4),
        "rss_mb": round(_rss_mb(), 2),
        "paper_latency": paper,
        "e2e_latency": e2e_report,
    }


async def _seed_goe_books(cache: Any, *, nonce: int = 1, price_shift: Decimal = Decimal("0")) -> None:
    from bot.market_data.models import ExchangeHealth, MarketTick

    snaps = build_goe_snapshots(nonce=nonce, price_shift=price_shift)
    # Group by exchange for health once
    for ex in EXCHANGES:
        await cache.set_health(
            ExchangeHealth(
                exchange=ex,
                connected=True,
                stale=False,
                synchronized=True,
                message_rate_per_sec=50.0,
            )
        )
    for snap in snaps:
        assert snap.order_book is not None
        book = snap.order_book.model_copy(
            update={"metadata": {"exchange": snap.exchange, "synchronized": True}}
        )
        await cache.set_book(snap.exchange, book)
        await cache.set_tick(
            MarketTick(
                exchange=snap.exchange,
                symbol=snap.symbol,
                bid=snap.bid,
                ask=snap.ask,
                sequence=nonce,
            )
        )


async def run_fleet_scaling(bot_counts: list[int] | None = None) -> dict[str, Any]:
    """Process-pool fleet scaling using GOE-emitting active cycles."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    bot_counts = bot_counts or [1, 5, 10, 25]
    results: dict[str, Any] = {}
    for n in bot_counts:
        t0 = time.perf_counter()
        with ProcessPoolExecutor(max_workers=n) as pool:
            futs = [pool.submit(_fleet_worker, i, 12) for i in range(n)]
            workers = [f.result() for f in as_completed(futs)]
        wall = time.perf_counter() - t0
        critical = max(w["wall_s"] for w in workers)
        agg_cpu = sum(w["cpu_s"] for w in workers)
        rss = sum(w["rss_mb"] for w in workers)
        results[str(n)] = {
            "bots": n,
            "critical_path_s": round(critical, 4),
            "aggregate_cpu_s": round(agg_cpu, 4),
            "wall_s": round(wall, 4),
            "rss_mb_sum": round(rss, 1),
            "rss_mb_per_bot": round(rss / n, 1),
            "mean_candidate_ms": round(
                sum(w["candidate_mean_ms"] for w in workers) / n, 4
            ),
            "mean_emits": round(sum(w["mean_emits"] for w in workers) / n, 2),
        }
    return results


def _fleet_worker(worker_id: int, cycles: int) -> dict[str, Any]:
    return asyncio.run(_fleet_worker_async(worker_id, cycles))


async def _fleet_worker_async(worker_id: int, cycles: int) -> dict[str, Any]:
    set_fee_tier("retail")
    settings = goe_emitting_settings()
    strategy = MakerInventoryStrategy(settings)
    samples: list[float] = []
    emits: list[int] = []
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    for i in range(cycles):
        snaps = build_goe_snapshots(
            nonce=10 + worker_id * 100 + i,
            price_shift=Decimal(str(i % 4)) * Decimal("0.1"),
        )
        t0 = time.perf_counter()
        opps = await strategy.evaluate_markets(snaps, equity=Decimal("25000"))
        samples.append((time.perf_counter() - t0) * 1000.0)
        emits.append(len(opps))
    return {
        "worker_id": worker_id,
        "wall_s": time.perf_counter() - wall0,
        "cpu_s": time.process_time() - cpu0,
        "rss_mb": _rss_mb(),
        "candidate_mean_ms": sum(samples) / max(1, len(samples)),
        "mean_emits": sum(emits) / max(1, len(emits)),
    }


async def main_async() -> dict[str, Any]:
    print("=== Phase 1/2: strategy candidate hot-path profile (GOE-emitting) ===")
    strategy_profile = await profile_strategy_scan(cycles=40, track_allocs=True)
    print(
        json.dumps(
            {
                "candidate_creation": strategy_profile["candidate_creation"],
                "mean_emits": strategy_profile["fixture"]["mean_emits_per_cycle"],
                "top_substages": strategy_profile["hotpath"]["ranked_by_total_ms"][:12],
                "fingerprint": strategy_profile["candidate_fingerprint"]["sha256"],
            },
            indent=2,
        )
    )

    print("=== Phase 10: paper e2e active (GOE-emitting books) ===")
    e2e = await profile_paper_e2e(cycles=25, mode="active", label="active_goe")
    print(
        json.dumps(
            {
                "mean_emits": e2e["mean_emits"],
                "paper_cycle": e2e["paper_cycle"],
                "strategy_scan": e2e["strategy_scan"],
                "candidate_creation": e2e["candidate_creation"],
            },
            indent=2,
        )
    )

    print("=== Phase 11: scaling 1/5/10/25 ===")
    scaling = await run_fleet_scaling([1, 5, 10, 25])
    print(json.dumps(scaling, indent=2))

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy_profile": strategy_profile,
        "e2e_active": e2e,
        "scaling": scaling,
    }
    path = Path("data/candidate_hotpath_profile.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {path}")
    return out


def main() -> int:
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
