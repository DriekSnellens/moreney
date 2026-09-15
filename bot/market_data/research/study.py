"""Build market-data research infrastructure report from recordings + audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.market_data.research import SCHEMA_VERSION
from bot.market_data.research.diagnostics import latency_report
from bot.market_data.research.manifest import build_manifest
from bot.market_data.research.ordering import analyze_ordering
from bot.market_data.research.quality import classify_dataset_quality, reject_horizon_if_uncertain
from bot.market_data.research.readiness import compute_readiness
from bot.market_data.research.replay import MarketDataReplayEngine
from bot.market_data.research.schema import DepthLevel, ResearchMarketEvent, TimestampQuality
from bot.market_data.research.sync import sync_coverage_report
from bot.market_data.research.venue_audit import venue_capability_report
from decimal import Decimal


def load_jsonl_events(root: Path | str) -> list[ResearchMarketEvent]:
    root = Path(root)
    if not root.exists():
        return []
    events: list[ResearchMarketEvent] = []
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            events.append(_from_dict(raw))
    return events


def _from_dict(raw: dict[str, Any]) -> ResearchMarketEvent:
    def lvl(items: list[Any]) -> tuple[DepthLevel, ...]:
        out = []
        for it in items or []:
            out.append(
                DepthLevel(
                    price=Decimal(str(it.get("price", 0))),
                    quantity=Decimal(str(it.get("quantity") or it.get("amount") or 0)),
                )
            )
        return tuple(out)

    def d(key: str) -> Decimal | None:
        v = raw.get(key)
        return None if v is None else Decimal(str(v))

    return ResearchMarketEvent(
        schema_version=str(raw.get("schema_version") or SCHEMA_VERSION),
        event_id=str(raw["event_id"]),
        venue=str(raw["venue"]),
        symbol=str(raw["symbol"]),
        channel=str(raw.get("channel") or "book_update"),
        exchange_ts_ns=raw.get("exchange_ts_ns"),
        received_ts_ns=int(raw["received_ts_ns"]),
        local_monotonic_ns=int(raw.get("local_monotonic_ns") or raw["received_ts_ns"]),
        sequence_number=raw.get("sequence_number"),
        bid_price=d("bid_price"),
        bid_size=d("bid_size"),
        ask_price=d("ask_price"),
        ask_size=d("ask_size"),
        bid_levels=lvl(raw.get("bid_levels") or []),
        ask_levels=lvl(raw.get("ask_levels") or []),
        source=str(raw.get("source") or "websocket"),
        connection_id=raw.get("connection_id"),
        book_age_ms=raw.get("book_age_ms"),
        receive_latency_ms=raw.get("receive_latency_ms"),
        crossed_book=bool(raw.get("crossed_book")),
        locked_book=bool(raw.get("locked_book")),
        stale=bool(raw.get("stale")),
        timestamp_quality=str(raw.get("timestamp_quality") or TimestampQuality.UNSUPPORTED.value),
        exchange_ts_available=bool(raw.get("exchange_ts_available")),
        prev_sequence=raw.get("prev_sequence"),
        is_snapshot=bool(raw.get("is_snapshot")),
        notes=tuple(raw.get("notes") or ()),
    )


def build_infrastructure_report(
    *,
    research_path: Path | str = "data/research_marketdata",
    venues: tuple[str, ...] = ("binance", "bitvavo", "okx"),
) -> dict[str, Any]:
    root = Path(research_path)
    events = load_jsonl_events(root)
    has = bool(events)
    audit = venue_capability_report(venues)
    quality = classify_dataset_quality(events, venues_required=venues)
    ordering = analyze_ordering(events).as_dict() if events else {}
    latency = latency_report(events) if events else {}
    sync = sync_coverage_report(events, venues=venues) if events else {"targets_sampled": 0}
    sync_rates = {
        k: float(v.get("usable_rate") or 0)
        for k, v in (sync.get("by_tolerance_ms") or {}).items()
    }
    readiness = compute_readiness(
        quality_grade=str(quality.get("grade") or "UNSUPPORTED"),
        sync_usable_rate_by_tol=sync_rates,
        has_recordings=has,
    )
    replay_fp = None
    if events:
        replay_fp = MarketDataReplayEngine(events).fingerprint()

    # Uncertainty proxy: if Bitvavo unsupported, treat uncertainty as large
    uncertainty_ms = 500.0 if not has else (
        500.0 if quality.get("grade") in {"UNSUPPORTED", "LOW"} else 50.0
    )
    horizon_gates = {
        h: reject_horizon_if_uncertain(h, timestamp_uncertainty_ms=uncertainty_ms)
        for h in (50, 100, 250, 500, 1000, 2000, 5000)
    }

    symbols = sorted({e.symbol for e in events})
    manifest = build_manifest(
        events,
        dataset_id=f"scan-{root.name}",
        venues=venues,
        symbols=symbols,
    )

    supported = [k for k, v in readiness["horizon_scores"].items() if v == "READY"]
    caution = [k for k, v in readiness["horizon_scores"].items() if v == "READY_WITH_CAUTION"]
    unsupported = [k for k, v in readiness["horizon_scores"].items() if v == "NOT_READY"]

    return {
        "A_problem": (
            "Lead-lag research returned INSUFFICIENT_DATA: no synchronized dual-timestamp "
            "tape; Bitvavo local clock; Redis hydrate historically overwrote received_at."
        ),
        "B_existing_architecture": (
            "WS → adapter → MarketDataService.handle_event → LocalOrderBook → Redis latest "
            "snapshot → shared hydrate. Research recorder must sit on publisher before Redis."
        ),
        "C_venue_capabilities": audit,
        "D_schema": {
            "schema_version": SCHEMA_VERSION,
            "format": "jsonl partitioned date/venue/symbol",
            "dual_clock": "exchange_ts_ns nullable + received_ts_ns + local_monotonic_ns",
            "depth": "L1 + up to L10 levels",
        },
        "E_recording_pipeline": {
            "flag": "RESEARCH_MARKETDATA_RECORDING_ENABLED",
            "path": str(root),
            "async_buffered": True,
            "affects_trading": False,
        },
        "F_redis_integration": {
            "role": "low-latency transport only",
            "not_research_db": True,
            "metadata_preserved": [
                "received_at",
                "exchange_ts_available",
                "timestamp_quality",
                "exchange_ts",
            ],
        },
        "G_replay": {
            "engine": "MarketDataReplayEngine",
            "modes": ["event_by_event", "until_ns", "visible_at"],
            "fingerprint": replay_fp,
            "causal": "future events invisible at t",
        },
        "H_synchronization": sync,
        "I_data_quality": quality,
        "J_horizon_readiness": readiness,
        "K_performance": {
            "hot_path": "enqueue only",
            "drops_exposed": True,
            "note": "No disk wait on trading path",
        },
        "L_failure_modes": [
            "missing exchange_ts (Bitvavo) → UNSUPPORTED not invented",
            "recorder queue overflow → dropped count, complete=false",
            "sequence gaps recorded in diagnostics",
        ],
        "M_reproducibility": {
            "manifest": manifest,
            "replay_fingerprint": replay_fp,
        },
        "N_horizon_gates": horizon_gates,
        "O_next_step_for_lead_lag": (
            "Enable publisher with RESEARCH_MARKETDATA_RECORDING_ENABLED=true, "
            "collect multi-hour synchronized tape for binance/bitvavo/okx on maker symbols, "
            "re-run readiness. Only then revisit lead-lag causal discovery — "
            "do not optimize alpha now."
        ),
        "ordering": ordering,
        "latency": latency,
        "supported_horizons": supported,
        "caution_horizons": caution,
        "unsupported_horizons": unsupported,
        "event_count": len(events),
        "final_verdict": readiness["verdict"],
        "market_data_lab_panel": _panel(latency, quality, readiness, sync),
        "label": "RESEARCH_INFRASTRUCTURE",
    }


def _panel(
    latency: dict[str, Any],
    quality: dict[str, Any],
    readiness: dict[str, Any],
    sync: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for venue, lat in (latency or {}).items():
        rows.append(
            {
                "venue": venue,
                "events": lat.get("n"),
                "exchange_ts_coverage": lat.get("exchange_ts_coverage"),
                "receive_ts_coverage": lat.get("receive_ts_coverage"),
                "sequence_coverage": lat.get("sequence_coverage"),
                "p50_ms": lat.get("p50_ms"),
                "p95_ms": lat.get("p95_ms"),
                "p99_ms": lat.get("p99_ms"),
                "quality_grade": quality.get("grade"),
            }
        )
    if not rows:
        for v in ("binance", "bitvavo", "okx"):
            rows.append(
                {
                    "venue": v,
                    "events": 0,
                    "exchange_ts_coverage": 0,
                    "receive_ts_coverage": 0,
                    "sequence_coverage": 0,
                    "p50_ms": None,
                    "p95_ms": None,
                    "p99_ms": None,
                    "quality_grade": "UNSUPPORTED",
                }
            )
    return rows
