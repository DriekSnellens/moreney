"""Predeclared research tape acceptance criteria — data quality only, never PnL."""

from __future__ import annotations

from typing import Any

from bot.market_data.research.operational_state import map_acceptance_to_final
from bot.market_data.research.quality import reject_horizon_if_uncertain

# Centralized, documented, deterministic. Do not tune after seeing strategy results.
PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA: dict[str, Any] = {
    "version": "research_accept_v1",
    "min_duration_seconds": 3600.0,  # >= 1 hour
    "min_total_events": 50_000,
    "min_events_per_core_venue": 5_000,  # binance/okx/bitvavo
    "core_venues": ("binance", "bitvavo", "okx"),
    "max_drop_rate": 0.01,  # drops / (written+drops)
    "max_write_errors": 0,
    "min_receive_ts_coverage": 0.99,
    "min_exchange_ts_coverage_by_venue": {
        "binance": 0.50,  # bookTicker often local — MEDIUM
        "okx": 0.90,
        "bitvavo": 0.0,  # explicitly unsupported exchange clock
    },
    "require_bitvavo_exchange_ts_null": True,
    "min_l1_present_rate": 0.70,
    "min_depth_present_rate": 0.30,  # share of events with >=1 depth level
    "max_ordering_gap_rate": 0.05,
    "max_duplicate_rate": 0.05,
    # Sync / horizon (exchange-clock venues only for fast horizons)
    "fast_horizons_ms": (50, 100, 250),
    "slow_horizons_ms": (500, 1000, 2000, 5000),
    "min_sync_usable_rate_slow": 0.20,
    "min_sync_usable_rate_fast": 0.50,
    "max_timestamp_uncertainty_ms_for_horizon_factor": 1.0,
    # Bitvavo has no exchange clock → default uncertainty for any route involving it
    "bitvavo_timestamp_uncertainty_ms": 500.0,
    "notes": (
        "Bitvavo has no exchange_ts; fast causal cross-venue with Bitvavo is NOT_READY. "
        "Slow horizons may be READY_WITH_CAUTION using receive clocks only when labeled. "
        "Criteria evaluate data quality only — never PnL, win rate, or signal strength."
    ),
}


HORIZONS_MS: tuple[int, ...] = (50, 100, 250, 500, 1000, 2000, 5000)

DIRECTED_ROUTES: tuple[tuple[str, str], ...] = (
    ("binance", "bitvavo"),
    ("binance", "okx"),
    ("bitvavo", "binance"),
    ("bitvavo", "okx"),
    ("okx", "binance"),
    ("okx", "bitvavo"),
)


def _pct(num: float, den: float) -> float:
    return (num / den) if den else 0.0


def evaluate_acceptance(
    *,
    inventory: dict[str, Any],
    integrity: dict[str, Any],
    sync_by_tolerance: dict[str, Any] | None = None,
    recorder_drops: int = 0,
    write_errors: int = 0,
    events_written_runtime: int = 0,
    criteria: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate tape against PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA only."""
    c = criteria or PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA
    failures: list[str] = []
    checks: dict[str, Any] = {}

    total = int(inventory.get("total_events") or 0)
    duration = inventory.get("duration_seconds")
    has_tape = total > 0

    checks["recording_duration"] = {
        "value": duration,
        "min": c["min_duration_seconds"],
        "ok": duration is not None and float(duration) >= float(c["min_duration_seconds"]),
    }
    if not checks["recording_duration"]["ok"]:
        failures.append("recording_duration")

    checks["event_volume"] = {
        "value": total,
        "min": c["min_total_events"],
        "ok": total >= int(c["min_total_events"]),
    }
    if not checks["event_volume"]["ok"]:
        failures.append("event_volume")

    by_venue = inventory.get("events_by_venue") or {}
    core = tuple(c["core_venues"])
    per_core = {v: int(by_venue.get(v) or 0) for v in core}
    checks["events_per_core_venue"] = {
        "value": per_core,
        "min": c["min_events_per_core_venue"],
        "ok": all(n >= int(c["min_events_per_core_venue"]) for n in per_core.values()),
    }
    if not checks["events_per_core_venue"]["ok"]:
        failures.append("events_per_core_venue")

    written_proxy = max(total, events_written_runtime)
    drop_rate = _pct(recorder_drops, written_proxy + recorder_drops)
    checks["recorder_drops"] = {
        "drops": recorder_drops,
        "drop_rate": drop_rate,
        "max_rate": c["max_drop_rate"],
        "ok": drop_rate <= float(c["max_drop_rate"]),
    }
    if not checks["recorder_drops"]["ok"]:
        failures.append("recorder_drops")

    checks["write_errors"] = {
        "value": write_errors,
        "max": c["max_write_errors"],
        "ok": write_errors <= int(c["max_write_errors"]),
    }
    if not checks["write_errors"]["ok"]:
        failures.append("write_errors")

    coverage = inventory.get("coverage_by_venue") or {}
    recv_ok = True
    ex_ok = True
    for venue in core:
        cov = coverage.get(venue) or {}
        recv = float(cov.get("received_ts_pct") or 0.0)
        if recv < float(c["min_receive_ts_coverage"]):
            recv_ok = False
        need = float((c["min_exchange_ts_coverage_by_venue"] or {}).get(venue, 0.0))
        ex = float(cov.get("exchange_ts_pct") or 0.0)
        if venue == "bitvavo" and c.get("require_bitvavo_exchange_ts_null"):
            # Bitvavo must stay at ~0 invented exchange timestamps
            if ex > 0.01:
                ex_ok = False
                failures.append("bitvavo_invented_exchange_ts")
        elif ex < need:
            ex_ok = False
    checks["timestamp_coverage"] = {"ok": recv_ok and ex_ok, "by_venue": coverage}
    if not recv_ok:
        failures.append("receive_ts_coverage")
    if not ex_ok and "bitvavo_invented_exchange_ts" not in failures:
        failures.append("exchange_ts_coverage")

    observed = max(1, int(integrity.get("observed") or 0))
    dup_rate = _pct(float(integrity.get("duplicates") or 0), observed)
    gap_rate = _pct(float(integrity.get("sequence_gaps") or 0), observed)
    checks["duplicates"] = {
        "rate": dup_rate,
        "max": c["max_duplicate_rate"],
        "ok": dup_rate <= float(c["max_duplicate_rate"]),
    }
    if not checks["duplicates"]["ok"]:
        failures.append("duplicates")
    checks["sequence_continuity"] = {
        "gap_rate": gap_rate,
        "max": c["max_ordering_gap_rate"],
        "ok": gap_rate <= float(c["max_ordering_gap_rate"]),
    }
    if not checks["sequence_continuity"]["ok"]:
        failures.append("sequence_gaps")

    l1_missing = float(integrity.get("missing_l1") or 0)
    l1_rate = 1.0 - _pct(l1_missing, observed)
    depth_rate = _pct(float(integrity.get("with_depth") or 0), observed)
    checks["l1_coverage"] = {
        "rate": l1_rate,
        "min": c["min_l1_present_rate"],
        "ok": l1_rate >= float(c["min_l1_present_rate"]),
    }
    if not checks["l1_coverage"]["ok"]:
        failures.append("l1_coverage")
    checks["depth_coverage"] = {
        "rate": depth_rate,
        "min": c["min_depth_present_rate"],
        "ok": depth_rate >= float(c["min_depth_present_rate"]),
    }
    if not checks["depth_coverage"]["ok"]:
        failures.append("depth_coverage")

    sync = sync_by_tolerance or {}
    bitvavo_unc = float(c["bitvavo_timestamp_uncertainty_ms"])
    horizon_detail: dict[str, Any] = {}
    for h in HORIZONS_MS:
        key = str(h)
        sync_row = sync.get(key) or sync.get(str(float(h))) or {}
        usable_rate = float(sync_row.get("usable_rate") or 0.0)
        gate = reject_horizon_if_uncertain(h, timestamp_uncertainty_ms=bitvavo_unc)
        # Any route involving Bitvavo inherits Bitvavo uncertainty for core triad sync
        if not gate["allowed"]:
            status = "NOT_READY"
            reason = gate["reason"]
        elif h in c["fast_horizons_ms"]:
            if usable_rate >= float(c["min_sync_usable_rate_fast"]) and bitvavo_unc <= h:
                status = "READY"
                reason = ""
            else:
                status = "NOT_READY"
                reason = (
                    f"fast_horizon_requires_exchange_clocks; "
                    f"usable_rate={usable_rate}; bitvavo_uncertainty_ms={bitvavo_unc}"
                )
        else:
            # Slow: allow CAUTION when volume/duration ok but Bitvavo clock unsupported
            if usable_rate >= float(c["min_sync_usable_rate_slow"]):
                status = "READY"
                reason = ""
            elif has_tape and duration and float(duration) >= float(c["min_duration_seconds"]):
                # Receive-clock slow research labeled caution — not fabricated sync
                status = "READY_WITH_CAUTION"
                reason = "receive_clock_only_bitvavo_unsupported_exchange_ts"
            else:
                status = "NOT_READY"
                reason = f"usable_rate={usable_rate}"
        horizon_detail[f"{h}ms"] = {
            "status": status,
            "usable_windows": sync_row.get("usable_windows"),
            "synchronization_coverage": usable_rate,
            "median_skew": sync_row.get("median_skew_ms"),
            "p95_skew": sync_row.get("p95_skew_ms"),
            "p99_skew": sync_row.get("p99_skew_ms"),
            "worst_skew": sync_row.get("p99_skew_ms"),
            "stale_counterpart_rate": sync_row.get("stale_counterpart_rate"),
            "uncertainty_estimate_ms": bitvavo_unc,
            "failure_reason": reason or None,
        }

    slow_ready = all(
        horizon_detail[f"{h}ms"]["status"] in {"READY", "READY_WITH_CAUTION"}
        for h in c["slow_horizons_ms"]
    ) and all(
        horizon_detail[f"{h}ms"]["status"] != "NOT_READY" for h in (1000, 2000, 5000)
    )
    # Stricter: slow READY only if at least 1s+ are caution/ready and base quality checks pass
    base_ok = not any(
        f in failures
        for f in (
            "recording_duration",
            "event_volume",
            "events_per_core_venue",
            "recorder_drops",
            "write_errors",
            "receive_ts_coverage",
            "bitvavo_invented_exchange_ts",
        )
    )
    slow_ready = base_ok and all(
        horizon_detail[f"{h}ms"]["status"] in {"READY", "READY_WITH_CAUTION"}
        for h in (1000, 2000, 5000)
    )
    fast_ready = base_ok and all(
        horizon_detail[f"{h}ms"]["status"] == "READY" for h in c["fast_horizons_ms"]
    )
    partial = base_ok and not slow_ready and any(
        horizon_detail[f"{h}ms"]["status"] in {"READY", "READY_WITH_CAUTION"}
        for h in HORIZONS_MS
    )

    final = map_acceptance_to_final(
        has_tape=has_tape,
        recorder_enabled=True,
        write_errors=write_errors,
        events_written_runtime=events_written_runtime,
        slow_ready=slow_ready,
        fast_ready=fast_ready,
        partial=partial or (base_ok and not slow_ready),
    )
    if not has_tape:
        final = "NO_REAL_TAPE"

    supported = [
        k for k, v in horizon_detail.items() if v["status"] in {"READY", "READY_WITH_CAUTION"}
    ]
    return {
        "criteria_version": c["version"],
        "checks": checks,
        "failures": failures,
        "horizon_detail": horizon_detail,
        "slow_ready": slow_ready,
        "fast_ready": fast_ready,
        "partial": partial,
        "final_verdict": final,
        "supported_horizons": supported,
        "next_action": _next_action(final, failures),
    }


def _next_action(verdict: str, failures: list[str]) -> str:
    if verdict == "NO_REAL_TAPE":
        return "Enable RESEARCH_MARKETDATA_RECORDING_ENABLED=true on the publisher and collect tape."
    if verdict == "RECORDER_BROKEN":
        return "Inspect recorder write_errors / output directory permissions; fix drain path."
    if verdict == "RECORDER_DISABLED":
        return "Set RESEARCH_MARKETDATA_RECORDING_ENABLED=true and restart the market-data publisher."
    if verdict == "DATA_READY_FOR_FAST_HORIZONS":
        return "Proceed to causal lead-lag research on supported fast horizons (still shadow-only)."
    if verdict == "DATA_READY_FOR_SLOW_HORIZONS":
        return (
            "Use chronological OOS split for slow-horizon research only; "
            "do not claim fast-horizon causal readiness while Bitvavo lacks exchange_ts."
        )
    if verdict == "DATA_PARTIALLY_READY":
        return "Extend recording duration / improve sync coverage; keep lead-lag execution disabled."
    if failures:
        return f"Resolve data-quality failures: {', '.join(failures[:5])}."
    return "Continue recording; re-run python -m bot.market_data.research.runner."


def build_cross_venue_matrix(
    *,
    sync_pair_reports: dict[str, dict[str, Any]] | None = None,
    symbols: list[str] | None = None,
    coverage_by_venue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Directed route matrix — unsupported routes stay explicit."""
    out: dict[str, Any] = {"routes": {}, "symbols": symbols or []}
    for src, dst in DIRECTED_ROUTES:
        key = f"{src}->{dst}"
        pair = (sync_pair_reports or {}).get(key) or {}
        bitvavo_involved = "bitvavo" in (src, dst)
        supported_h: list[str] = []
        for h in HORIZONS_MS:
            if bitvavo_involved and h < 500:
                status = "NOT_READY"
            elif bitvavo_involved:
                status = "READY_WITH_CAUTION"
            else:
                rate = float(((pair.get("by_tolerance_ms") or {}).get(str(h)) or {}).get("usable_rate") or 0)
                status = "READY" if rate >= 0.5 else ("READY_WITH_CAUTION" if rate >= 0.2 else "NOT_READY")
            if status != "NOT_READY":
                supported_h.append(f"{h}ms")
        out["routes"][key] = {
            "source": src,
            "destination": dst,
            "overlapping_observation_windows": pair.get("targets_sampled"),
            "synchronization_quality": pair.get("by_tolerance_ms"),
            "missing_counterpart_pct": None,
            "stale_counterpart_pct": None,
            "supported_horizons": supported_h,
            "bitvavo_exchange_ts": (
                (coverage_by_venue or {}).get("bitvavo", {}).get("exchange_ts_pct")
                if bitvavo_involved
                else None
            ),
            "note": (
                "Bitvavo exchange_ts unsupported — no fabricated sync"
                if bitvavo_involved
                else None
            ),
        }
    return out
