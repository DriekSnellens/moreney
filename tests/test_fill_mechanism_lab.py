"""Fill mechanism sensitivity lab — isolation, causality, no fabrication."""

from __future__ import annotations

import hashlib
import inspect
import json
from decimal import Decimal
from pathlib import Path

from bot.opportunity.fill_lab.audit import SupportLevel, audit_dataset
from bot.opportunity.fill_lab.baseline import (
    baseline_fingerprint,
    extract_baseline_fills,
    extract_quotes,
    load_paper,
)
from bot.opportunity.fill_lab.events import QuoteEvent
from bot.opportunity.fill_lab.models import (
    BookPoint,
    FillModelId,
    run_depth_consumption,
    run_touch_only,
    run_touch_persistence,
    run_trade_through_baseline,
)
from bot.opportunity.fill_lab.study import build_study


def _write_mini_paper(path: Path) -> Path:
    paper = {
        "orders": {
            "o1": {
                "id": "o1",
                "opportunity_id": "opp1",
                "symbol": "BTCEUR",
                "side": "buy",
                "requested_price": "100",
                "requested_quantity": "0.01",
                "filled_quantity": "0.01",
                "status": "filled",
                "strategy": "maker_inventory",
                "exchange": "kraken",
                "extra": {
                    "post_only": True,
                    "placed_ms": "1700000000000",
                    "venue": "kraken",
                    "last_fill_type": "trade_through",
                },
            }
        },
        "fills": {
            "f1": {
                "id": "f1",
                "order_id": "o1",
                "symbol": "BTCEUR",
                "side": "buy",
                "price": "100",
                "quantity": "0.01",
                "fee": "0.016",
                "exchange": "kraken",
                "created_at": "2026-03-01T10:00:01+00:00",
                "extra": {"fill_type": "trade_through", "post_only": True},
            }
        },
        "tracker": {
            "trades": [
                {
                    "opportunity_id": "opp1",
                    "symbol": "BTCEUR",
                    "timestamp": "2026-03-01T10:05:00+00:00",
                    "realized_net_profit": "-0.05",
                    "realized_fees": "0.03",
                    "realized_adverse": "0.02",
                    "status": "completed",
                }
            ]
        },
        "markout": {"by_horizon": {"1000": ["10"], "5000": ["12"], "30000": ["15"], "60000": ["18"]}},
    }
    path.write_text(json.dumps(paper), encoding="utf-8")
    return path


def test_data_audit_marks_touch_and_depth_unsupported_without_books(tmp_path: Path) -> None:
    paper = _write_mini_paper(tmp_path / "paper.json")
    audit = audit_dataset(paper)
    assert audit["models"]["TRADE_THROUGH_ONLY"]["support"] == SupportLevel.SUPPORTED.value
    assert audit["models"]["TOUCH_ONLY"]["support"] == SupportLevel.UNSUPPORTED.value
    assert audit["models"]["TOUCH_PERSISTENCE_100"]["support"] == SupportLevel.UNSUPPORTED.value
    assert audit["models"]["DEPTH_CONSUMPTION"]["support"] == SupportLevel.UNSUPPORTED.value
    assert audit["checks"]["top_of_book_updates"] is False
    assert audit["checks"]["depth_levels"] is False


def test_baseline_fingerprint_stable(tmp_path: Path) -> None:
    paper = _write_mini_paper(tmp_path / "paper.json")
    a = baseline_fingerprint(paper)
    b = baseline_fingerprint(paper)
    assert a == b
    assert a["model"] == FillModelId.TRADE_THROUGH_ONLY.value
    assert a["baseline_fill_count"] == 1
    assert a["completed_round_trips"] == 1


def test_quote_generation_identical_across_models(tmp_path: Path) -> None:
    paper = _write_mini_paper(tmp_path / "paper.json")
    data = load_paper(paper)
    quotes = extract_quotes(data)
    assert len(quotes) == 1
    h = hashlib.sha256(
        json.dumps([q.as_dict() for q in quotes], sort_keys=True).encode()
    ).hexdigest()
    # Running experimental models must not mutate quote stream
    books: dict[str, list] = {}
    run_touch_only(quotes, books, supported=False)
    run_touch_persistence(quotes, books, persistence_ms=100, supported=False)
    run_depth_consumption(quotes, books, supported=False)
    h2 = hashlib.sha256(
        json.dumps([q.as_dict() for q in quotes], sort_keys=True).encode()
    ).hexdigest()
    assert h == h2


def test_unsupported_models_do_not_fabricate_fills() -> None:
    q = QuoteEvent(
        quote_id="q1",
        opportunity_id="opp",
        timestamp_ms=1000.0,
        symbol="BTCEUR",
        side="buy",
        venue="kraken",
        price=Decimal("100"),
        quantity=Decimal("0.01"),
        strategy="maker_inventory",
    )
    for res in (
        run_touch_only([q], {}, supported=False),
        run_touch_persistence([q], {}, persistence_ms=250, supported=False),
        run_depth_consumption([q], {}, supported=True),
    ):
        assert res.fills == []
        assert res.support == SupportLevel.UNSUPPORTED.value


def test_touch_persistence_causal_no_pre_quote_touch() -> None:
    q = QuoteEvent(
        quote_id="q1",
        opportunity_id="opp",
        timestamp_ms=1000.0,
        symbol="BTCEUR",
        side="buy",
        venue="kraken",
        price=Decimal("100"),
        quantity=Decimal("0.01"),
        strategy="maker",
    )
    books = {
        "q1": [
            BookPoint(
                timestamp_ms=500,
                bid=Decimal("100"),
                ask=Decimal("100.1"),
                mid=Decimal("100.05"),
            ),
            BookPoint(
                timestamp_ms=1500,
                bid=Decimal("99.9"),
                ask=Decimal("100.05"),
                mid=Decimal("99.975"),
            ),
        ]
    }
    res = run_touch_persistence([q], books, persistence_ms=100, supported=True)
    assert res.fills == []


def test_touch_persistence_requires_duration() -> None:
    q = QuoteEvent(
        quote_id="q1",
        opportunity_id="opp",
        timestamp_ms=1000.0,
        symbol="BTCEUR",
        side="buy",
        venue="kraken",
        price=Decimal("100"),
        quantity=Decimal("0.01"),
        strategy="maker",
    )
    brief = {
        "q1": [
            BookPoint(1100, Decimal("100"), Decimal("100.1"), Decimal("100.05")),
            BookPoint(1150, Decimal("99.9"), Decimal("100.1"), Decimal("100.0")),
        ]
    }
    assert run_touch_persistence([q], brief, persistence_ms=100, supported=True).fills == []

    sustained = {
        "q1": [
            BookPoint(1100, Decimal("100"), Decimal("100.1"), Decimal("100.05")),
            BookPoint(1200, Decimal("100"), Decimal("100.1"), Decimal("100.05")),
            BookPoint(1250, Decimal("100"), Decimal("100.05"), Decimal("100.025")),
        ]
    }
    res = run_touch_persistence([q], sustained, persistence_ms=100, supported=True)
    assert len(res.fills) == 1
    assert res.fills[0].model_id == FillModelId.TOUCH_PERSISTENCE_100.value
    assert res.fills[0].quote_age_ms == 200.0
    assert res.fills[0].observational is True
    assert res.status == "EXPERIMENTAL_COUNTERFACTUAL"


def test_depth_never_fabricates_without_queue() -> None:
    q = QuoteEvent(
        quote_id="q1",
        opportunity_id="opp",
        timestamp_ms=1000.0,
        symbol="BTCEUR",
        side="sell",
        venue="kraken",
        price=Decimal("101"),
        quantity=Decimal("0.01"),
        strategy="maker",
    )
    books = {
        "q1": [
            BookPoint(
                1100,
                Decimal("100"),
                Decimal("101"),
                Decimal("100.5"),
                bid_size=Decimal("1"),
                ask_size=Decimal("5"),
                traded_volume_since_quote=Decimal("10"),
            )
        ]
    }
    res = run_depth_consumption([q], books, supported=True, queue_position_known=False)
    assert res.fills == []
    assert "queue" in res.notes[0].lower()


def test_baseline_fill_type_deterministic(tmp_path: Path) -> None:
    paper = _write_mini_paper(tmp_path / "paper.json")
    fills = extract_baseline_fills(load_paper(paper))
    assert len(fills) == 1
    assert fills[0].fill_type == "trade_through"
    assert fills[0].model_id == FillModelId.TRADE_THROUGH_ONLY.value
    assert fills[0].observational is False
    tt = run_trade_through_baseline(fills)
    assert tt.status == "CONSERVATIVE_BASELINE"


def test_deterministic_replay(tmp_path: Path) -> None:
    paper = _write_mini_paper(tmp_path / "paper.json")
    a = build_study(str(paper))
    b = build_study(str(paper))
    assert a["baseline_fingerprint"] == b["baseline_fingerprint"]
    assert a["C_eligibility_counts"] == b["C_eligibility_counts"]
    assert a["H_production_recommendation"] == b["H_production_recommendation"]


def test_experimental_models_not_in_execution_path() -> None:
    import bot.execution.executor as ex_mod
    import bot.execution.paper_executor as paper_ex_mod
    import bot.paper.runner as runner_mod

    for mod in (ex_mod, paper_ex_mod):
        src = inspect.getsource(mod)
        assert "fill_lab" not in src
        assert "TOUCH_PERSISTENCE" not in src
        assert "run_touch_only" not in src
    # Runner may *display* fill_model_lab snapshot but must not call experimental fill models
    src = inspect.getsource(runner_mod)
    assert "run_touch_only" not in src
    assert "run_touch_persistence" not in src
    assert "TOUCH_PERSISTENCE" not in src
    assert "_fill_model_lab_snapshot" in src


def test_production_pnl_uses_baseline_only(tmp_path: Path) -> None:
    paper = _write_mini_paper(tmp_path / "paper.json")
    report = build_study(str(paper))
    assert report["H_production_recommendation"]["primary"] in {
        "REQUIRE BETTER DATA",
        "KEEP TRADE-THROUGH BASELINE",
        "ABANDON MAKER THESIS UNDER CURRENT ECONOMICS",
    }
    panel = {row["model"]: row for row in report["fill_model_lab_panel"]}
    assert panel["TRADE_THROUGH_ONLY"]["status"] == "CONSERVATIVE_BASELINE"
    for mid, row in panel.items():
        if mid == "TRADE_THROUGH_ONLY":
            continue
        assert row["status"] in {"EXPERIMENTAL_COUNTERFACTUAL", "UNSUPPORTED"}
        if row["support"] == "UNSUPPORTED":
            assert row["sample_count"] == 0


def test_study_success_letter_c_without_market_data(tmp_path: Path) -> None:
    paper = _write_mini_paper(tmp_path / "paper.json")
    report = build_study(str(paper))
    assert report["H_production_recommendation"]["success_criterion"] == "C"
    assert report["H_production_recommendation"]["primary"] == "REQUIRE BETTER DATA"
    assert report["H_production_recommendation"]["also"] == "KEEP TRADE-THROUGH BASELINE"
    assert report["G_trade_through_toxicity_selector"]["answer"] == "INSUFFICIENT_DATA"


def test_audit_dataset_on_real_paper_if_present() -> None:
    path = Path("data/paper_25000live.json")
    if not path.exists():
        return
    audit = audit_dataset(path)
    assert audit["models"]["TRADE_THROUGH_ONLY"]["support"] == "SUPPORTED"
    assert audit["models"]["TOUCH_ONLY"]["support"] == "UNSUPPORTED"
    fp = baseline_fingerprint(path)
    assert fp["completed_round_trips"] == 17
    study = build_study(str(path))
    assert study["H_production_recommendation"]["primary"] == "REQUIRE BETTER DATA"
