"""Tests for live execution diagnosis (synthetic audit fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

from bot.research.live_execution_diagnosis.buy_fill_gap import analyze_buy_fill_gap
from bot.research.live_execution_diagnosis.errors import analyze_exchange_errors
from bot.research.live_execution_diagnosis.runner import run_diagnosis


def _write_audit(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_classify_okx_clordid_error(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    _write_audit(
        audit,
        [
            {
                "ts": "2026-08-22T22:00:00+00:00",
                "type": "micro_order_exception",
                "payload": {
                    "error": "ExchangeError",
                    "message": (
                        'okx {"code":"1","data":[{"clOrdId":"micro-abc123",'
                        '"sCode":"51000","sMsg":"Parameter clOrdId error"}]}'
                    ),
                },
            },
            {
                "ts": "2026-08-22T22:00:01+00:00",
                "type": "order_submit",
                "payload": {"venue": "okx", "symbol": "SOLEUR"},
            },
        ],
    )
    report = analyze_exchange_errors(audit)
    assert report.total_exceptions == 1
    assert report.buckets[0].category == "OKX_CLORDID_REJECTED"
    assert report.buckets[0].count == 1


def test_buy_fill_gap_asymmetry(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    _write_audit(
        audit,
        [
            {
                "ts": "2026-08-21T10:00:00+00:00",
                "type": "micro_order_result",
                "payload": {
                    "venue": "bitvavo",
                    "side": "buy",
                    "status": "submitted",
                    "symbol": "NEAREUR",
                    "notional_eur": "50",
                },
            },
            {
                "ts": "2026-08-21T10:08:00+00:00",
                "type": "micro_order_result",
                "payload": {
                    "venue": "bitvavo",
                    "side": "sell",
                    "status": "filled",
                    "symbol": "NEAREUR",
                    "notional_eur": "68.48",
                },
            },
            {
                "ts": "2026-08-21T10:09:00+00:00",
                "type": "order_blocked",
                "payload": {"reason": "max open orders reached"},
            },
        ],
    )
    bridge = tmp_path / "bridge.json"
    bridge.write_text(
        json.dumps(
            {
                "live_fill_count": 1,
                "backfill_mirrored_count": 1,
                "live_maker": True,
                "skips": {"time_stop_below_be": 10},
            }
        ),
        encoding="utf-8",
    )
    report = analyze_buy_fill_gap(audit, bridge_state_path=bridge)
    assert report.buy_filled == 0
    assert report.sell_filled == 1
    assert report.buy_submitted == 1
    assert any(rc["id"] == "MAKER_BUY_RESTING" for rc in report.root_causes)


def test_run_diagnosis_writes_outputs(tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "audit.jsonl"
    _write_audit(
        audit,
        [
            {
                "ts": "2026-08-22T22:00:00+00:00",
                "type": "micro_order_exception",
                "payload": {"error": "ExchangeError", "message": "bitvavo clientOrderId parameter is invalid."},
            },
        ],
    )
    json_out = tmp_path / "out.json"
    md_out = tmp_path / "out.md"
    run_diagnosis(
        audit_path=audit,
        session_status_path=tmp_path / "missing.json",
        bridge_state_path=tmp_path / "missing.json",
        json_out=json_out,
        md_out=md_out,
    )
    assert json_out.is_file()
    assert md_out.is_file()
    assert "BITVAVO_CLIENT_ORDER_ID_INVALID" in md_out.read_text()
