"""Run H-0005 / H-0007 on a fresh split with controls. Execution off."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.llm.hypothesis_memory import HypothesisRegistry
from bot.research.regime_lab.families import (
    FreshnessCVDFamily,
    NoTradeBaseline,
    RegimeOnlyDescriptive,
    WideSpreadMRFamily,
)
from bot.research.regime_lab.metrics import attach_event_economics, window_metrics
from bot.research.regime_lab.protocol import (
    EXECUTION_MODEL,
    FORENSIC_OOS_END_NS,
    H0005,
    H0007,
    LOOKBACK_BUFFER_NS,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    build_manifest,
)
from bot.research.regime_lab.split import make_fresh_split
from bot.research.regime_lab.stability import stability_report
from bot.research.regime_lab.verdict import mechanical_verdict
from bot.research.tournament.engine import _load_horizon_readiness
from bot.research.tournament.families import (
    CrossVenueDislocationFamily,
    ShortHorizonMeanReversionFamily,
)
from bot.research.tournament.tape_index import build_tape_index


def _venues(params: dict[str, Any], kind: str) -> tuple[str, str | None]:
    if kind == "cvd":
        return str(params.get("venue_a") or "binance"), str(params.get("venue_b") or "bitvavo")
    return str(params.get("venue") or "bitvavo"), None


def _run_family(fam, *, index, split, readiness) -> Any:
    fam._index = index
    return fam.run(
        index=index,
        split=split,
        horizon_readiness=readiness,
        dataset_meta={"dataset_id": index.dataset_id},
    )


def _window_eval(fam, index, window: dict[str, Any], params: dict[str, Any], kind: str, *, inclusive: bool) -> dict[str, Any]:
    if inclusive:
        stats, events = fam.evaluate_window(
            index,
            start_ns=int(window["start_ts_ns"]),
            end_ns_exclusive=None,
            end_ns_inclusive=int(window["end_ts_ns_inclusive"]),
            params=params,
            horizons=[int(params.get("horizon_ms") or 500)],
        )
    else:
        stats, events = fam.evaluate_window(
            index,
            start_ns=int(window["start_ts_ns"]),
            end_ns_exclusive=int(window["end_ts_ns_exclusive"]),
            end_ns_inclusive=None,
            params=params,
            horizons=[int(params.get("horizon_ms") or 500)],
        )
    venue, vx = _venues(params, kind)
    h = int(params.get("horizon_ms") or 500)
    events = attach_event_economics(events, venue=venue, venue_exit=vx, horizon_ms=h)
    for e in events:
        ts = e.get("ts_ns")
        if ts is not None and int(ts) <= int(FORENSIC_OOS_END_NS):
            raise RuntimeError("forensic timestamp leaked into labeled window")
    audit = dict(getattr(fam, "last_audit", {}) or {})
    m = window_metrics(
        events,
        venue=venue,
        venue_exit=vx,
        mean_forward=stats.conditional_forward_mean,
        horizon_ms=h,
        audit=audit,
    )
    m["audit"] = audit
    m["stability"] = stability_report(
        events,
        oos_start_ns=int(window.get("start_ts_ns") or 0) or None,
        oos_end_ns=int(
            window.get("end_ts_ns_inclusive") or window.get("end_ts_ns_exclusive") or 0
        )
        or None,
    )
    return m


def register_candidates(registry: HypothesisRegistry | None = None) -> dict[str, str]:
    registry = registry or HypothesisRegistry()
    ids = {}
    for spec in (H0005, H0007):
        existing = None
        for row in registry.list_all():
            if row.get("hypothesis_id") == spec["hypothesis_id"] and row.get("event") != "annotate":
                existing = row
        rec = {
            "hypothesis_id": spec["hypothesis_id"],
            "parent_hypothesis_id": spec["parent_hypothesis_id"],
            "economic_mechanism": spec["economic_mechanism"],
            "pre_trade_features": spec["pre_trade_features"],
            "signal_definition": spec["signal_definition"],
            "cost_model": "shared_tournament_waterfall",
            "risk_model": "tournament_stability_v1",
            "execution_model": EXECUTION_MODEL,
            "expected_failure_modes": [
                "NON_PARTICIPATION_ONLY",
                "NO_SELECTIVE_EDGE",
                "OOS_FAILED",
                "COST_NEGATIVE",
                "UNSTABLE",
            ],
            "research_status": "CANDIDATE",
            "status": "CANDIDATE",
            "strategy_family": spec["strategy_id"],
            "source": "regime_hypothesis_lab",
            "inherits_parent_pnl": False,
            "affects_production_ranking": False,
        }
        if existing and existing.get("status") == "CANDIDATE":
            ids[spec["hypothesis_id"]] = spec["hypothesis_id"]
            continue
        registry.append(rec)
        ids[spec["hypothesis_id"]] = spec["hypothesis_id"]
    return ids


def run_regime_lab(
    *,
    research_path: Path | str = "data/research_marketdata",
    readiness_report: Path | str = "data/market_data_research_report.json",
    out_dir: Path | str | None = None,
    max_events: int | None = None,
    stride: int = 4,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    register_candidates()
    readiness = _load_horizon_readiness(Path(readiness_report))
    min_ts = int(FORENSIC_OOS_END_NS) - int(LOOKBACK_BUFFER_NS)
    index = build_tape_index(
        Path(research_path),
        max_events=max_events,
        stride=stride,
        min_ts_ns=min_ts,
        parse_inventory_events=False,
    )
    split = make_fresh_split(index)
    manifest = build_manifest(
        dataset_id=index.dataset_id,
        dataset_fingerprint=index.content_fingerprint,
        split=split,
        extra={"stride": stride, "min_ts_ns": min_ts},
    )
    data_status = split.get("DATA_STATUS") or (
        "FRESH_SPLIT_READY" if split.get("available") else "INSUFFICIENT_FRESH_DATA"
    )

    blocked = {
        "STATUS": data_status,
        "PACKAGE": PACKAGE_LABEL,
        "protocol_version": PROTOCOL_VERSION,
        "manifest": manifest,
        "DATA_STATUS": data_status,
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": False,
        "inherits_parent_pnl": False,
        "forensic_period_used_as_oos": False,
        "LLM_USED": "NO",
        "NEW_HYPOTHESES": [],
    }
    if not split.get("available") or index.peak_points == 0:
        blocked["H-0005"] = _empty_card("INSUFFICIENT_FRESH_DATA")
        blocked["H-0007"] = _empty_card("INSUFFICIENT_FRESH_DATA")
        blocked["CONTROL_RESULTS"] = {}
        blocked["NEXT_ACTION"] = "Keep recording unseen tape after forensic OOS end."
        _write(blocked, out_dir)
        return blocked

    h5 = FreshnessCVDFamily()
    h7 = WideSpreadMRFamily()
    parent_cvd = CrossVenueDislocationFamily()
    parent_mr = ShortHorizonMeanReversionFamily()
    no_trade = NoTradeBaseline()
    reg_fresh = RegimeOnlyDescriptive(regime="fresh")
    reg_wide = RegimeOnlyDescriptive(regime="wide")

    r5 = _run_family(h5, index=index, split=split, readiness=readiness)
    r7 = _run_family(h7, index=index, split=split, readiness=readiness)
    rp5 = _run_family(parent_cvd, index=index, split=split, readiness=readiness)
    rp7 = _run_family(parent_mr, index=index, split=split, readiness=readiness)
    rnt = _run_family(no_trade, index=index, split=split, readiness=readiness)
    rr5 = _run_family(reg_fresh, index=index, split=split, readiness=readiness)
    rr7 = _run_family(reg_wide, index=index, split=split, readiness=readiness)

    cards = {}
    controls: dict[str, Any] = {}
    for hid, fam, res, parent_res, regime_res, kind in (
        ("H-0005", h5, r5, rp5, rr5, "cvd"),
        ("H-0007", h7, r7, rp7, rr7, "mr"),
    ):
        params = res.frozen_params or (fam.param_grid(list(fam.required_horizons()))[0])
        oos_m = _window_eval(fam, index, split["untouched_oos"], params, kind, inclusive=True)
        oos_audit = dict(oos_m.get("audit") or getattr(fam, "last_audit", {}) or {})
        dev_m = _window_eval(fam, index, split["development"], params, kind, inclusive=False)
        parent_oos = _window_eval(
            parent_cvd if kind == "cvd" else parent_mr,
            index,
            split["untouched_oos"],
            parent_res.frozen_params or params,
            kind,
            inclusive=True,
        )
        regime_oos = _window_eval(
            regime_res and (reg_fresh if kind == "cvd" else reg_wide),
            index,
            split["untouched_oos"],
            (regime_res.frozen_params or params),
            kind,
            inclusive=True,
        )
        no_trade_oos = {
            "signals": 0,
            "NET": 0.0,
            "EXPECTED_NET": 0.0,
            "mean_forward": 0.0,
        }
        stab = oos_m.get("stability") or stability_report([])
        verdict = mechanical_verdict(
            data_status=data_status,
            tournament_verdict=res.verdict,
            failed_gate=res.failed_gate,
            gated_metrics=oos_m,
            parent_metrics=parent_oos,
            regime_only_metrics=regime_oos,
            audit=oos_audit,
            stability=stab,
        )
        cards[hid] = {
            "hypothesis_id": hid,
            "parent_hypothesis_id": "H-0001" if hid == "H-0005" else "H-0003",
            "strategy_id": fam.strategy_id,
            "DATA_STATUS": data_status,
            "DEV_RESULT": _window_card(dev_m),
            "OOS_RESULT": _window_card(oos_m, stability=stab),
            "VERDICT": verdict,
            "NET/fill": oos_m.get("NET_per_fill"),
            "SAMPLE_COUNT": oos_m.get("signals"),
            "STABILITY": stab,
            "TOP_CONCENTRATION": {
                "symbol": stab.get("top_symbol"),
                "symbol_share": stab.get("top_symbol_share"),
                "route": stab.get("top_route"),
                "route_share": stab.get("top_route_share"),
                "time_block_share": stab.get("top_block_share"),
                "ROUTE_UNIVERSE_LIMITED": stab.get("ROUTE_UNIVERSE_LIMITED"),
                "positive_block_count": stab.get("positive_block_count"),
                "negative_block_count": stab.get("negative_block_count"),
            },
            "tournament_verdict": res.verdict,
            "failed_gate": res.failed_gate,
            "frozen_params": res.frozen_params,
            "audit": oos_audit,
            "metrics_oos": oos_m,
            "metrics_dev": dev_m,
            "inherits_parent_pnl": False,
            "DISCOVERY_NET": None,
            "DISCOVERY_LABEL": "FORENSICS_NOT_USED_AS_PROFIT",
        }
        controls[hid] = {
            "parent": {
                "strategy_id": parent_res.strategy_id,
                "verdict": parent_res.verdict,
                "EXPECTED_NET": parent_oos.get("EXPECTED_NET"),
                "signals": parent_oos.get("signals"),
            },
            "gated": {
                "strategy_id": fam.strategy_id,
                "verdict": verdict,
                "EXPECTED_NET": oos_m.get("EXPECTED_NET"),
                "signals": oos_m.get("signals"),
            },
            "no_trade": no_trade_oos,
            "regime_only": {
                "strategy_id": (reg_fresh if kind == "cvd" else reg_wide).strategy_id,
                "EXPECTED_NET": regime_oos.get("EXPECTED_NET"),
                "signals": regime_oos.get("signals"),
                "mean_forward": regime_oos.get("mean_forward"),
            },
        }

    llm = _maybe_llm(cards, enabled=llm_enabled)
    out = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": manifest,
        "DATA_STATUS": data_status,
        "split": {
            "development": split.get("development"),
            "freeze_boundary": split.get("freeze_boundary"),
            "untouched_oos": split.get("untouched_oos"),
            "forensic_oos_end_ns": FORENSIC_OOS_END_NS,
            "fresh_duration_seconds": split.get("fresh_duration_seconds"),
        },
        "H-0005": cards["H-0005"],
        "H-0007": cards["H-0007"],
        "CONTROL_RESULTS": controls,
        "LLM_USED": llm.get("used"),
        "llm_advisory": llm,
        "NEW_HYPOTHESES": llm.get("new_ids") or [],
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": False,
        "inherits_parent_pnl": False,
        "forensic_period_used_as_oos": False,
        "affects_production_ranking": False,
        "NEXT_ACTION": _next(cards),
        "notes": [
            "Parents were not modified.",
            "Forensic window is DISCOVERY only and is not OOS.",
            "Do not display forensic NET as strategy profitability.",
        ],
    }
    _write(out, out_dir)
    return out


def _empty_card(verdict: str) -> dict[str, Any]:
    return {
        "DATA_STATUS": verdict,
        "DEV_RESULT": None,
        "OOS_RESULT": None,
        "VERDICT": verdict,
        "NET/fill": None,
        "SAMPLE_COUNT": 0,
        "STABILITY": None,
        "TOP_CONCENTRATION": None,
    }


def _next(cards: dict[str, Any]) -> str:
    vs = {c.get("VERDICT") for c in cards.values()}
    if "INSUFFICIENT_FRESH_DATA" in vs:
        return "Keep recording unseen tape; do not recycle the forensic period."
    if "OOS_PASS" in vs:
        return "OOS_PASS is not live alpha. Do not enable execution. Replicate on more unseen tape."
    return "Parents remain REJECTED. New hypotheses remain CANDIDATE. Do not enable execution."


def _window_card(m: dict[str, Any], *, stability: dict[str, Any] | None = None) -> dict[str, Any]:
    stab = stability or m.get("stability") or {}
    audit = m.get("audit") or {}
    admitted = int(audit.get("admitted") or m.get("accepted_events") or m.get("signals") or 0)
    return {
        "EXPECTED_NET": m.get("EXPECTED_NET"),
        "EXPECTED_NET_world": m.get("EXPECTED_NET_world"),
        "EXPECTED_NET_quantity": m.get("EXPECTED_NET_quantity"),
        "NET": m.get("NET"),
        "NET_world": m.get("NET_world"),
        "NET_quantity": m.get("NET_quantity"),
        "signals": m.get("signals"),
        "candidate_events": m.get("candidate_events"),
        "accepted_events": m.get("accepted_events"),
        "completed_round_trips": m.get("completed_round_trips"),
        "gross": m.get("gross"),
        "fees": m.get("fees"),
        "slippage": m.get("slippage"),
        "adverse": m.get("adverse"),
        "canonical_replay_net_per_fill_eur": m.get("NET_per_fill"),
        "canonical_replay_net_per_fill_world": m.get("NET_per_fill_world"),
        "NET/fill": m.get("NET_per_fill"),
        "NET/fill_world": m.get("NET_per_fill_world"),
        "NET/fill_quantity": m.get("NET_per_fill_quantity"),
        "mean_edge_execution_replay_net_per_fill_eur": m.get(
            "mean_edge_execution_replay_net_per_fill_eur"
        ),
        "NET/bps": m.get("NET_per_bps"),
        "EV": m.get("EV"),
        "EV_world": m.get("EV_world"),
        "EV_capture": m.get("EV_capture"),
        "maximum_drawdown": m.get("maximum_drawdown"),
        "block_stability": {
            "positive_block_count": stab.get("positive_block_count"),
            "negative_block_count": stab.get("negative_block_count"),
            "top_block_share": stab.get("top_block_share"),
        },
        "symbol_concentration": {
            "count": stab.get("symbol_count"),
            "top": stab.get("top_symbol"),
            "top_share": stab.get("top_symbol_share"),
        },
        "route_concentration": {
            "count": stab.get("route_count"),
            "top": stab.get("top_route"),
            "top_share": stab.get("top_route_share"),
            "ROUTE_UNIVERSE_LIMITED": stab.get("ROUTE_UNIVERSE_LIMITED"),
        },
        "time_concentration": {"top_block_share": stab.get("top_block_share")},
        "regime_concentration": {
            "admitted": admitted,
            "rejected": audit.get("rejected"),
            "unsupported": audit.get("unsupported"),
            "gate_share_among_admitted": 1.0 if admitted else None,
        },
    }


def _maybe_llm(cards: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"used": "NO", "status": "DISABLED"}
    try:
        from bot.core.config import get_settings
        from bot.research.llm.ollama import build_provider_from_settings
        from bot.research.llm.prompts import REGIME_LAB_ADVISORY_SYSTEM
        from bot.research.llm.schemas import ForensicsAdvisory

        provider = build_provider_from_settings(get_settings())
        health = provider.health()
        if not health.available:
            return {"used": "NO", "status": health.status, "detail": health.detail}
        batch = provider.generate_structured(
            system_prompt=REGIME_LAB_ADVISORY_SYSTEM,
            context={
                "label": "REGIME_HYPOTHESIS_LAB",
                "authoritative_verdicts": {k: v.get("VERDICT") for k, v in cards.items()},
                "summary": {
                    k: {
                        "VERDICT": v.get("VERDICT"),
                        "DATA_STATUS": v.get("DATA_STATUS"),
                        "DEV_NET": (v.get("DEV_RESULT") or {}).get("EXPECTED_NET"),
                        "OOS_NET": (v.get("OOS_RESULT") or {}).get("EXPECTED_NET"),
                        "SAMPLE_COUNT": v.get("SAMPLE_COUNT"),
                        "STABILITY": (v.get("STABILITY") or {}).get("label"),
                        "TOP_CONCENTRATION": v.get("TOP_CONCENTRATION"),
                        "failed_gate": v.get("failed_gate"),
                    }
                    for k, v in cards.items()
                },
                "rules": [
                    "Do not change thresholds, OOS, or verdicts.",
                    "At most two new hypotheses.",
                    "Execution stays off.",
                    "New hypotheses inherit no PnL and must start at DEV/OOS.",
                ],
            },
            schema_model=ForensicsAdvisory,
        )
        registry = HypothesisRegistry()
        new_ids: list[str] = []
        for advice in list(batch.hypotheses or [])[:2]:
            hid = registry.next_id()
            registry.append(
                {
                    "hypothesis_id": hid,
                    "parent_hypothesis_id": advice.parent_hypothesis_id,
                    "economic_mechanism": advice.economic_mechanism,
                    "pre_trade_features": list(advice.pre_trade_features),
                    "signal_definition": advice.what_changed,
                    "cost_model": "shared_tournament_waterfall",
                    "risk_model": "tournament_stability_v1",
                    "execution_model": EXECUTION_MODEL,
                    "expected_failure_modes": [advice.expected_failure_mode],
                    "research_status": "PROPOSED",
                    "status": "PROPOSED",
                    "strategy_family": advice.strategy_family,
                    "source": "regime_lab_llm_advisory",
                    "inherits_parent_pnl": False,
                    "affects_production_ranking": False,
                    "title": advice.title,
                    "what_we_learn_if_fails": advice.what_we_learn_if_fails,
                    "note": "Must start again at DEV/OOS. Advisory only.",
                }
            )
            new_ids.append(hid)
        return {
            "used": "YES",
            "status": health.status,
            "label": "ADVISORY_NON_AUTHORITATIVE",
            "advisory": batch.model_dump(),
            "new_ids": new_ids,
        }
    except Exception as exc:  # noqa: BLE001
        return {"used": "NO", "status": f"UNAVAILABLE:{exc}"}


def _write(out: dict[str, Any], out_dir: Path | str | None) -> None:
    dest = Path(out_dir or "data/regime_hypothesis_lab")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results.json").write_text(
        json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    compact = {
        "label": PACKAGE_LABEL,
        "STATUS": out.get("STATUS"),
        "DATA_STATUS": out.get("DATA_STATUS"),
        "rows": [
            _dash_row(out.get("H-0005") or {}, "H-0005"),
            _dash_row(out.get("H-0007") or {}, "H-0007"),
        ],
        "CONTROL_RESULTS": out.get("CONTROL_RESULTS"),
        "LLM_USED": out.get("LLM_USED"),
        "NEW_HYPOTHESES": out.get("NEW_HYPOTHESES"),
        "PRODUCTION_EXECUTION": "DISABLED",
        "NEXT_ACTION": out.get("NEXT_ACTION"),
        "disclaimer": "OBSERVED/DEV/OOS/HYPOTHESIS are separated. Forensic NET is not strategy PnL.",
        "manifest": out.get("manifest"),
    }
    if dest.resolve() == Path("data/regime_hypothesis_lab").resolve() or dest.resolve().is_relative_to(
        Path("data/regime_hypothesis_lab").resolve()
    ):
        Path("data/regime_hypothesis_lab_report.json").write_text(
            json.dumps(compact, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )


def _dash_row(card: dict[str, Any], hid: str) -> dict[str, Any]:
    stab = card.get("STABILITY") or {}
    return {
        "ID": hid,
        "Parent": card.get("parent_hypothesis_id"),
        "Mechanism": (H0005 if hid == "H-0005" else H0007)["economic_mechanism"][:160],
        "Status": card.get("VERDICT") or "CANDIDATE",
        "Discovery_NET": card.get("DISCOVERY_NET"),
        "DEV_NET": (card.get("DEV_RESULT") or {}).get("EXPECTED_NET"),
        "DEV_NET_world": "SIGNAL_EXPECTATION",
        "OOS_NET": (card.get("OOS_RESULT") or {}).get("EXPECTED_NET"),
        "OOS_NET_world": "SIGNAL_EXPECTATION",
        "OOS_NET_quantity": "ExpectedNetPerSignalEUR",
        "canonical_replay_net_eur": (card.get("OOS_RESULT") or {}).get("NET"),
        "canonical_replay_net_world": "EXECUTION_REPLAY",
        "canonical_replay_net_per_fill_eur": (card.get("OOS_RESULT") or {}).get(
            "canonical_replay_net_per_fill_eur"
        )
        or (card.get("OOS_RESULT") or {}).get("NET/fill"),
        "canonical_replay_net_per_fill_world": "EXECUTION_REPLAY",
        "mean_edge_execution_replay_net_per_fill_eur": (card.get("OOS_RESULT") or {}).get(
            "mean_edge_execution_replay_net_per_fill_eur"
        ),
        "NET_per_fill": None,
        "sample_count": card.get("SAMPLE_COUNT"),
        "stability": stab.get("label"),
        "top_concentration": card.get("TOP_CONCENTRATION"),
        "verdict": card.get("VERDICT"),
        "DATA_STATUS": card.get("DATA_STATUS"),
    }
