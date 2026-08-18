"""Run H-0005 alpha attribution on the paired parent universe. Execution off."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.research.accounting.engine import _SPECS, _assert_frozen, _check_leakage
from bot.research.accounting.paired import PairedPartition, aggregate_paired, pair_window
from bot.research.accounting.protocol import REPLAY_VERSION, SCHEMA_VERSION, WATERFALL_TOLERANCE
from bot.research.alpha_attribution.attribution import feature_attribution
from bot.research.alpha_attribution.contexts import leave_one_context_out, summarize_contexts
from bot.research.alpha_attribution.features import attach_attribution_features, classify_membership
from bot.research.alpha_attribution.groups import assert_parent_identity, group_economics
from bot.research.alpha_attribution.observations import ranked_observations
from bot.research.alpha_attribution.paired_audit import audit_paired_windows
from bot.research.alpha_attribution.protocol import (
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    PUBLISHED_PAIRED_DELTA_EUR,
    assert_no_oos_threshold_creation,
    build_manifest,
)
from bot.research.alpha_attribution.report import write_report
from bot.research.regime_lab.families import FreshnessCVDFamily
from bot.research.regime_lab.features import views_for
from bot.research.regime_lab.metrics import attach_event_economics
from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_START_NS,
    LOOKBACK_BUFFER_NS,
    STRIDE,
)
from bot.research.robustness.windows import sequential_windows
from bot.research.tournament.tape_index import build_tape_index


def _mean_fwd(events: list[dict[str, Any]]) -> float | None:
    if not events:
        return None
    return sum(float(e.get("forward") or 0.0) for e in events) / len(events)


def _collect_live(
    *,
    index,
    plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[Any], list[tuple[str, list[dict[str, Any]]]], bool]:
    spec = _SPECS["H-0005"]
    fam = FreshnessCVDFamily()
    params = dict(spec["params"])
    _assert_frozen("H-0005", params)
    venue, vx = spec["venue"], spec["venue_exit"]
    h = int(params.get("horizon_ms") or 5000)
    views = views_for(index)
    paired_rows = []
    window_parent: list[tuple[str, list[dict[str, Any]]]] = []
    all_parent: list[dict[str, Any]] = []
    no_leakage = True
    for w in plan.get("windows") or []:
        if not w.get("complete"):
            continue
        part_raw = fam.partition_window(
            index,
            start_ns=int(w["start_ts_ns"]),
            end_ns_exclusive=None,
            end_ns_inclusive=int(w["end_ts_ns_inclusive"]),
            params=params,
            horizons=[h],
        )
        retained_raw = list(part_raw["child_events"])
        excluded_raw = list(part_raw["excluded_events"])
        unsupported_raw = list(part_raw["unsupported_events"])
        parent_raw = list(part_raw["parent_events"])
        # Enrich parent candidates so features exist; child/excluded already enriched.
        parent_feat = [
            attach_attribution_features(
                e, index=index, views=views, venue=venue, peer_venue=vx
            )
            for e in parent_raw
        ]
        # Prefer partition admission when present; otherwise classify from age.
        retained_ids = {
            (e.get("ts_ns"), e.get("symbol"), e.get("route") or e.get("venue"))
            for e in retained_raw
        }
        excluded_ids = {
            (e.get("ts_ns"), e.get("symbol"), e.get("route") or e.get("venue"))
            for e in excluded_raw
        }
        labeled: list[dict[str, Any]] = []
        for e in parent_feat:
            key = (e.get("ts_ns"), e.get("symbol"), e.get("route") or e.get("venue"))
            if key in retained_ids:
                e["admission"] = "ADMITTED"
                e["membership"] = "RETAINED_BY_CHILD"
            elif key in excluded_ids:
                e["admission"] = "REJECTED"
                e["membership"] = "EXCLUDED_BY_CHILD"
            else:
                e["membership"] = classify_membership(admission=str(e.get("admission") or "UNSUPPORTED_DATA"))
            labeled.append(e)
        parent_e = attach_event_economics(labeled, venue=venue, venue_exit=vx, horizon_ms=h)
        retained_e = [e for e in parent_e if e.get("membership") == "RETAINED_BY_CHILD"]
        excluded_e = [e for e in parent_e if e.get("membership") == "EXCLUDED_BY_CHILD"]
        unsupported_e = [e for e in parent_e if e.get("membership") not in {"RETAINED_BY_CHILD", "EXCLUDED_BY_CHILD"}]
        if not _check_leakage(parent_e):
            no_leakage = False
            raise RuntimeError(f"H-0005 {w['WINDOW_ID']}: forensic timestamp leaked")
        audit = part_raw["audit"]
        partition = PairedPartition(
            parent_events=tuple(parent_e),
            child_events=tuple(retained_e),
            excluded_events=tuple(excluded_e),
            unsupported_events=tuple(unsupported_e),
            candidates=int(audit.get("candidates") or len(parent_e)),
            admitted=int(audit.get("admitted") or len(retained_e)),
            rejected=int(audit.get("rejected") or len(excluded_e)),
            unsupported=int(audit.get("unsupported") or len(unsupported_e)),
        )
        row = pair_window(
            window_id=str(w["WINDOW_ID"]),
            complete=True,
            start_ts_ns=int(w["start_ts_ns"]),
            end_ts_ns_inclusive=int(w["end_ts_ns_inclusive"]),
            partition=partition,
            venue=venue,
            venue_exit=vx,
            mean_forward_parent=_mean_fwd(parent_e),
            mean_forward_child=_mean_fwd(retained_e),
            mean_forward_excluded=_mean_fwd(excluded_e),
        )
        paired_rows.append(row)
        window_parent.append((str(w["WINDOW_ID"]), parent_e))
        all_parent.extend(parent_e)
    return all_parent, paired_rows, window_parent, no_leakage


def run_alpha_attribution(
    *,
    research_path: Path | str = "data/research_marketdata",
    canonical_results: Path | str = "data/research/canonical_accounting_results.json",
    out_dir: Path | str | None = None,
    live: bool = True,
    max_events: int | None = None,
    stride: int = STRIDE,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    canon_path = Path(canonical_results)
    canon = json.loads(canon_path.read_text(encoding="utf-8")) if canon_path.exists() else {}
    h5 = canon.get("H-0005") or {}
    stored_windows = (h5.get("PAIRED_PARENT_CHILD") or {}).get("windows") or []
    stored_agg = (h5.get("PAIRED_PARENT_CHILD") or {}).get("aggregate") or {}
    reported_delta = stored_agg.get("aggregate_delta") or PUBLISHED_PAIRED_DELTA_EUR
    stored_audit = audit_paired_windows(
        stored_windows,
        reported_aggregate_delta=reported_delta,
    )

    live_meta: dict[str, Any] = {"used": False}
    live_audit: dict[str, Any] | None = None
    groups: dict[str, Any] = {}
    features: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    loo: dict[str, Any] = {}
    obs: list[dict[str, Any]] = []
    identity_issues: list[str] = []
    venue = "okx"
    venue_exit = "bitvavo"

    if live and Path(research_path).exists():
        min_ts = int(FIRST_LAB_OOS_START_NS) - int(LOOKBACK_BUFFER_NS)
        index = build_tape_index(
            Path(research_path),
            max_events=max_events,
            stride=stride,
            min_ts_ns=min_ts,
            parse_inventory_events=False,
        )
        plan = sequential_windows(index)
        live_meta = {
            "used": True,
            "dataset_id": index.dataset_id,
            "dataset_fingerprint": index.content_fingerprint,
            "stride": stride,
            "n_windows": len(plan.get("windows") or []),
        }
        all_parent, paired_rows, window_parent, no_leakage = _collect_live(index=index, plan=plan)
        paired_dicts = [r.to_dict() for r in paired_rows]
        live_agg = aggregate_paired(paired_rows)
        live_audit = audit_paired_windows(
            paired_dicts, reported_aggregate_delta=reported_delta
        )
        live_delta = Decimal(str(live_agg.get("aggregate_delta") or 0))
        published = Decimal(str(reported_delta))
        if abs(live_delta - published) > WATERFALL_TOLERANCE:
            live_audit.setdefault("issues", []).append(
                f"live aggregate_delta={live_agg.get('aggregate_delta')} "
                f"!= published={reported_delta}"
            )
            live_audit["PAIRED_DELTA_ACCOUNTING_AUDIT"] = "FAIL"
        retained = [e for e in all_parent if e.get("membership") == "RETAINED_BY_CHILD"]
        excluded = [e for e in all_parent if e.get("membership") == "EXCLUDED_BY_CHILD"]
        unsupported = [
            e
            for e in all_parent
            if e.get("membership") not in {"RETAINED_BY_CHILD", "EXCLUDED_BY_CHILD"}
        ]
        parent_g = group_economics(
            all_parent, venue=venue, venue_exit=venue_exit, label="ALL_PARENT"
        )
        retained_g = group_economics(
            retained,
            venue=venue,
            venue_exit=venue_exit,
            label="RETAINED_BY_CHILD",
            parent_signals=parent_g["signal_count"],
            parent_net=Decimal(str(parent_g["replay_net_eur"])),
        )
        excluded_g = group_economics(
            excluded,
            venue=venue,
            venue_exit=venue_exit,
            label="EXCLUDED_BY_CHILD",
            parent_signals=parent_g["signal_count"],
            parent_net=Decimal(str(parent_g["replay_net_eur"])),
        )
        unsupported_g = group_economics(
            unsupported,
            venue=venue,
            venue_exit=venue_exit,
            label="UNSUPPORTED",
            parent_signals=parent_g["signal_count"],
            parent_net=Decimal(str(parent_g["replay_net_eur"])),
        )
        identity_issues = assert_parent_identity(
            parent_g,
            retained_g,
            excluded_g,
            unsupported_net=Decimal(str(unsupported_g["replay_net_eur"])),
            unsupported_n=int(unsupported_g["signal_count"]),
        )
        # Window-level nets for groups
        def _win_subset(pred):
            rows = []
            for wid, evs in window_parent:
                sub = [e for e in evs if pred(e)]
                g = group_economics(sub, venue=venue, venue_exit=venue_exit, label="w")
                rows.append({"window": wid, "replay_net_eur": g["replay_net_eur"], "signals": g["signal_count"], "fills": g["estimated_fills"]})
            return rows

        parent_g["windows"] = _win_subset(lambda e: True)
        retained_g["windows"] = _win_subset(lambda e: e.get("membership") == "RETAINED_BY_CHILD")
        excluded_g["windows"] = _win_subset(lambda e: e.get("membership") == "EXCLUDED_BY_CHILD")
        for g in (parent_g, retained_g, excluded_g):
            pos = sum(1 for w in g["windows"] if Decimal(str(w["replay_net_eur"])) > 0)
            neg = sum(1 for w in g["windows"] if Decimal(str(w["replay_net_eur"])) < 0)
            g["positive_windows"] = pos
            g["negative_windows"] = neg
        groups = {
            "ALL_PARENT": parent_g,
            "RETAINED_BY_CHILD": retained_g,
            "EXCLUDED_BY_CHILD": excluded_g,
            "UNSUPPORTED": unsupported_g,
        }
        win_ret = [
            (wid, [e for e in evs if e.get("membership") == "RETAINED_BY_CHILD"])
            for wid, evs in window_parent
        ]
        win_exc = [
            (wid, [e for e in evs if e.get("membership") == "EXCLUDED_BY_CHILD"])
            for wid, evs in window_parent
        ]
        features = feature_attribution(
            retained,
            excluded,
            venue=venue,
            venue_exit=venue_exit,
            window_retained=win_ret,
            window_excluded=win_exc,
        )
        contexts = summarize_contexts(
            all_parent, venue=venue, venue_exit=venue_exit, window_events=window_parent
        )
        loo = leave_one_context_out(
            all_parent, venue=venue, venue_exit=venue_exit, window_events=window_parent
        )
        obs = ranked_observations(
            excluded_positive=Decimal(str(excluded_g["replay_net_eur"])) > 0,
            excluded_net=str(excluded_g["replay_net_eur"]),
            retained_net=str(retained_g["replay_net_eur"]),
            parent_net=str(parent_g["replay_net_eur"]),
            top_contexts=contexts,
            feature_diffs=features,
            dependency=loo,
        )
        live_meta["no_leakage"] = no_leakage
        live_meta["n_complete_windows"] = len(window_parent)
        live_meta["n_parent_signals"] = len(all_parent)
    else:
        live_audit = {"PAIRED_DELTA_ACCOUNTING_AUDIT": "NOT_RUN"}

    primary_audit = live_audit if live_meta.get("used") else stored_audit
    if live_meta.get("used") and stored_audit.get("PAIRED_DELTA_ACCOUNTING_AUDIT") == "FAIL":
        if primary_audit is not None:
            primary_audit = dict(primary_audit)
            primary_audit["PAIRED_DELTA_ACCOUNTING_AUDIT"] = "FAIL"
            primary_audit.setdefault("issues", []).extend(stored_audit.get("issues") or [])
    elapsed = time.perf_counter() - t0
    parent_net = (groups.get("ALL_PARENT") or {}).get("replay_net_eur") or stored_audit.get(
        "sum_parent_replay_net_eur"
    )
    retained_net = (groups.get("RETAINED_BY_CHILD") or {}).get("replay_net_eur") or stored_audit.get(
        "sum_child_replay_net_eur"
    )
    excluded_net = (groups.get("EXCLUDED_BY_CHILD") or {}).get("replay_net_eur") or stored_audit.get(
        "sum_excluded_signal_net_eur"
    )
    excl_pos = False
    try:
        excl_pos = Decimal(str(excluded_net or 0)) > 0
    except Exception:
        excl_pos = False
    why = (
        "Sign convention: paired_delta = child_replay_net - parent_replay_net. "
        "H-0005 is a pure freshness filter (child_only=0), so child net equals "
        "retained/shared net and parent net equals retained + excluded "
        "(plus unsupported, if any). "
        f"On this paired complete-window universe parent={parent_net} EUR, "
        f"retained/child={retained_net} EUR, excluded={excluded_net} EUR. "
        "H-0005 underperformed because the gate dropped excluded parent signals "
        f"whose canonical replay net is {'positive' if excl_pos else 'not positive'}, "
        "not because retained trades are loss-making in aggregate EUR. "
        "Do not retune quote_age_ms on this OOS."
    )
    out = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": build_manifest(extra=live_meta),
        "PAIRED_DELTA_ACCOUNTING_AUDIT": (primary_audit or {}).get("PAIRED_DELTA_ACCOUNTING_AUDIT"),
        "stored_paired_audit": stored_audit,
        "live_paired_audit": live_audit,
        "identity_issues": identity_issues,
        "PARENT_REPLAY_NET": parent_net,
        "H-0005_REPLAY_NET": retained_net,
        "EXCLUDED_SIGNAL_NET": excluded_net,
        "RETAINED_SIGNAL_NET": retained_net,
        "WHY_H0005_UNDERPERFORMED": why,
        "groups": groups,
        "feature_attribution": features,
        "ranked_gate_forensics": features[:20],
        "contexts": contexts,
        "leave_one_context_out": loo,
        "CONTEXT_DEPENDENCY": loo.get("CONTEXT_DEPENDENCY"),
        "NEW_RESEARCH_OBSERVATIONS": obs,
        "NEW_STRATEGIES_CREATED": [],
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": False,
        "DESCRIPTIVE_ONLY": True,
        "oos_thresholds_created": False,
        "h0005_modified": False,
        "h0007_optimized": False,
        "NO_NEW_ALPHA_CLAIMED": True,
        "PERFORMANCE": {
            "alpha_attribution_seconds": elapsed,
            "hot_path_untouched": True,
        },
        "live": live_meta,
        "first_lab_canonical_h0005_net": (
            ((h5.get("first_lab_oos") or {}).get("EXECUTION_REPLAY_WORLD") or {})
            .get("replay_net_eur")
            or {}
        ).get("value"),
    }
    if identity_issues:
        out["PAIRED_DELTA_ACCOUNTING_AUDIT"] = "FAIL"
        out["identity_fail"] = identity_issues
    assert_no_oos_threshold_creation(out)
    _write(out, out_dir)
    return out


def compact_from_result(out: dict[str, Any]) -> dict[str, Any]:
    """Dashboard contract — values must equal the canonical result objects."""
    groups = out.get("groups") or {}
    return {
        "label": PACKAGE_LABEL,
        "STATUS": out.get("STATUS"),
        "PAIRED_DELTA_ACCOUNTING_AUDIT": out.get("PAIRED_DELTA_ACCOUNTING_AUDIT"),
        "PRODUCTION_EXECUTION": "DISABLED",
        "NO_NEW_ALPHA_CLAIMED": True,
        "DESCRIPTIVE_ONLY": True,
        "PARENT_REPLAY_NET": out.get("PARENT_REPLAY_NET"),
        "H-0005_REPLAY_NET": out.get("H-0005_REPLAY_NET"),
        "EXCLUDED_SIGNAL_NET": out.get("EXCLUDED_SIGNAL_NET"),
        "RETAINED_SIGNAL_NET": out.get("RETAINED_SIGNAL_NET"),
        "WHY_H0005_UNDERPERFORMED": out.get("WHY_H0005_UNDERPERFORMED"),
        "CONTEXT_DEPENDENCY": out.get("CONTEXT_DEPENDENCY"),
        "groups": [
            {
                "GROUP": g,
                "SIGNALS": (groups.get(g) or {}).get("signal_count"),
                "FILLS": (groups.get(g) or {}).get("estimated_fills"),
                "NET": (groups.get(g) or {}).get("replay_net_eur"),
                "NET_world": "EXECUTION_REPLAY",
                "NET_quantity": "RealizedReplayNetEUR",
                "NET/SIGNAL": (groups.get(g) or {}).get("replay_net_per_signal"),
                "NET/FILL": (groups.get(g) or {}).get("replay_net_per_fill"),
                "POSITIVE_WINDOWS": (groups.get(g) or {}).get("positive_windows"),
                "NEGATIVE_WINDOWS": (groups.get(g) or {}).get("negative_windows"),
            }
            for g in ("ALL_PARENT", "RETAINED_BY_CHILD", "EXCLUDED_BY_CHILD")
        ],
        "contexts": [
            {
                "context": c.get("context"),
                "NET_contribution": c.get("NET_contribution"),
                "stability": c.get("stability"),
                "concentration": c.get("concentration"),
                "pre_trade_usable": c.get("pre_trade_usable"),
                "DESCRIPTIVE_ONLY": True,
                "signals": c.get("signal_count"),
            }
            for c in (out.get("contexts") or [])[:8]
        ],
        "observations": [
            {"title": o.get("title"), "type": "RESEARCH_OBSERVATION", "finding": o.get("finding")}
            for o in (out.get("NEW_RESEARCH_OBSERVATIONS") or [])[:6]
        ],
        "disclaimer": (
            "Forensic attribution. DESCRIPTIVE_ONLY. No new alpha claimed. "
            "Execution disabled. Do not read NET as proven edge."
        ),
    }


def _write(out: dict[str, Any], out_dir: Path | str | None) -> None:
    dest = Path(out_dir or "data/research")
    dest.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, indent=2, sort_keys=True, default=str) + "\n"
    (dest / "alpha_attribution_results.json").write_text(payload, encoding="utf-8")
    Path("data/research").mkdir(parents=True, exist_ok=True)
    Path("data/research/alpha_attribution_results.json").write_text(payload, encoding="utf-8")
    compact = compact_from_result(out)
    Path("data/alpha_attribution_report.json").write_text(
        json.dumps(compact, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    write_report(out, "docs/ALPHA_ATTRIBUTION_REPORT.md")
