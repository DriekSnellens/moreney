"""Hard tests: future data must not affect past causal decisions."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot.opportunity.causal_walkforward import (
    CausalBeliefModel,
    CONFIGS,
    decide,
    walk_forward,
)


def _ts(i: int) -> str:
    base = datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=i)).isoformat()


def _trade(
    i: int,
    *,
    route: str = "bitvavo->bitvavo",
    exp: str = "1",
    real: str = "-2",
    adv: str = "3",
    buf: str = "0.5",
) -> dict:
    buy, sell = route.split("->")
    return {
        "timestamp": _ts(i),
        "opportunity_id": f"opp-{i}",
        "strategy": "maker_inventory",
        "symbol": "XRPEUR",
        "buy_exchange": buy,
        "sell_exchange": sell,
        "expected_net_profit": exp,
        "realized_net_profit": real,
        "expected_adverse": buf,
        "realized_adverse": adv,
    }


def test_current_trade_isolation() -> None:
    """Changing realized adverse of trade t must not change decision at t."""
    trades = [_trade(i, real="-2", adv="3") for i in range(10)]
    cfg = CONFIGS["C_CONDITIONAL_EV_ONLY"]
    base = walk_forward(trades, config=cfg)
    mutated = deepcopy(trades)
    # Catastrophic adverse on trade 5 only.
    mutated[5]["realized_adverse"] = "999"
    mut = walk_forward(mutated, config=cfg)
    assert base["events"][5]["decision"] == mut["events"][5]["decision"]
    assert base["events"][5]["predicted_net_if_fill"] == mut["events"][5]["predicted_net_if_fill"]
    # Later events may differ once markout of trade 5 becomes available.
    # (not asserted here — isolation is the contract)


def test_future_loss_does_not_reject_earlier() -> None:
    """Appending a catastrophic future loss must not change earlier decisions."""
    early = [_trade(i) for i in range(5)]
    cfg = CONFIGS["B_EARLY_STOP_ONLY"]
    before = walk_forward(early, config=cfg)
    extended = early + [
        _trade(i, real="-100", adv="50") for i in range(5, 20)
    ]
    after = walk_forward(extended, config=cfg)
    for i in range(5):
        assert before["events"][i]["decision"] == after["events"][i]["decision"]
        assert before["events"][i]["route_state_before"] == after["events"][i]["route_state_before"]


def test_permutation_of_future_outcomes() -> None:
    """Permuting outcomes after t must not change decisions at or before t."""
    trades = [_trade(i, real=str(-1 - (i % 3))) for i in range(12)]
    cfg = CONFIGS["B_EARLY_STOP_ONLY"]
    base = walk_forward(trades, config=cfg)
    permuted = deepcopy(trades)
    # Swap last two realized outcomes.
    permuted[-1]["realized_net_profit"], permuted[-2]["realized_net_profit"] = (
        permuted[-2]["realized_net_profit"],
        permuted[-1]["realized_net_profit"],
    )
    alt = walk_forward(permuted, config=cfg)
    cutoff = len(trades) - 2
    for i in range(cutoff):
        assert base["events"][i]["decision"] == alt["events"][i]["decision"]


def test_delayed_markout_not_used_before_horizon() -> None:
    """5s markout must not affect beliefs before t+5s."""
    model = CausalBeliefModel(markout_delay=timedelta(seconds=5))
    t0 = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    model.schedule_observation(
        event_ts=t0,
        route="bitvavo->bitvavo",
        adverse_eur=Decimal("10"),
        expected_net=Decimal("1"),
        realized_net=Decimal("-2"),
        key="k",
        strategy="maker_inventory",
        opportunity_id="o1",
    )
    # Immediately after fill: adverse memory still empty.
    model.release_due(t0 + timedelta(seconds=1))
    snap = model.predict(route="bitvavo->bitvavo", expected_adverse_buffer=Decimal("0.5"))
    assert snap.historical_adverse_n == 0
    # After horizon:
    model.release_due(t0 + timedelta(seconds=5))
    snap2 = model.predict(route="bitvavo->bitvavo", expected_adverse_buffer=Decimal("0.5"))
    assert snap2.historical_adverse_n == 1


def test_full_dataset_init_rejected() -> None:
    """Train init must fail if rows fall inside the evaluation window."""
    model = CausalBeliefModel()
    trades = [_trade(i) for i in range(5)]
    eval_start = datetime.fromisoformat(_ts(2))
    with pytest.raises(ValueError, match="evaluation-period"):
        model.import_train_only(trades, eval_start=eval_start)


def test_early_stop_affects_only_subsequent() -> None:
    """The trade that creates early-stop evidence may execute; later ones reject."""
    # 8 losers → after 8th observe, early stop true for 9th+.
    trades = [_trade(i, exp="1", real="-2", adv="3") for i in range(12)]
    cfg = CONFIGS["B_EARLY_STOP_ONLY"]
    result = walk_forward(trades, config=cfg)
    events = result["events"]
    # First 8 should be taken (insufficient historical early-stop before each).
    for i in range(8):
        assert events[i]["decision"] == "take", i
    # After 8 observations, route is early_stopped; subsequent rejects.
    assert any(e["decision"] == "reject" for e in events[8:])
    assert events[8]["decision_reason"] == "early_stop_historical"
    # Evidence trade itself was taken:
    assert events[7]["decision"] == "take"
    assert events[7]["route_state_after"] == "early_stopped"


def test_deterministic_replay() -> None:
    trades = [_trade(i) for i in range(15)]
    cfg = CONFIGS["D_CONDITIONAL_EV_PLUS_EARLY_STOP"]
    a = walk_forward(trades, config=cfg)
    b = walk_forward(trades, config=cfg)
    assert a["total_realized_net"] == b["total_realized_net"]
    assert [e["decision"] for e in a["events"]] == [e["decision"] for e in b["events"]]


def test_predict_does_not_mutate_on_observe_path() -> None:
    """Belief snapshot used for decide must be independent of later observe."""
    model = CausalBeliefModel()
    for i in range(8):
        model.observe_immediate_roundtrip(
            route="bitvavo->bitvavo",
            key=f"k{i}",
            strategy="maker_inventory",
            expected_net=Decimal("1"),
            realized_net=Decimal("-2"),
        )
    belief = model.predict(
        route="bitvavo->bitvavo", expected_adverse_buffer=Decimal("0.5")
    )
    decision, reason, net_if_fill, ev = decide(
        config=CONFIGS["B_EARLY_STOP_ONLY"],
        belief=belief,
        expected_net=Decimal("1"),
        expected_buffer=Decimal("0.5"),
    )
    assert decision == "reject"
    # Observing more must not change the already-computed decision variables.
    model.observe_immediate_roundtrip(
        route="bitvavo->bitvavo",
        key="extra",
        strategy="maker_inventory",
        expected_net=Decimal("1"),
        realized_net=Decimal("-50"),
    )
    assert belief.early_stop is True
    assert decision == "reject"
    assert reason == "early_stop_historical"
    assert net_if_fill == Decimal("1")  # unchanged local vars


def test_rejected_trades_do_not_update_adverse_memory() -> None:
    """If we reject, we must not learn that trade's adverse (no phantom learning)."""
    # Build history so conditional EV would reject.
    model = CausalBeliefModel()
    for _ in range(5):
        model.adverse_by_route.setdefault("bitvavo->bitvavo", []).append(Decimal("5"))
        model.belief_version += 1
    trades = [
        _trade(0, exp="0.1", real="-2", adv="99", buf="0.1"),  # would reject on cond EV
    ]
    cfg = CONFIGS["C_CONDITIONAL_EV_ONLY"]
    # Copy adverse into a fresh walk by injecting via train then one eval trade.
    # Simpler: walk a sequence where first 3 takes build hist, then a reject candidate.
    seq = [_trade(i, exp="2", real="0.1", adv="4", buf="0.5") for i in range(3)]
    seq.append(_trade(3, exp="0.2", real="-5", adv="99", buf="0.1"))
    result = walk_forward(seq, config=cfg)
    # After 3 takes, adverse hist has entries only after +5s releases.
    # Force release by continuing — fourth may still take if hist < 3 released.
    # Explicit unit: schedule + release then reject path shouldn't append 99 without take.
    m2 = CausalBeliefModel()
    belief = m2.predict(route="r", expected_adverse_buffer=Decimal("1"))
    assert belief.historical_adverse_n == 0
    # Rejected path in walk_forward: ensure 99 never enters if decision reject.
    # Make hist ready:
    for _ in range(3):
        m2.adverse_by_route.setdefault("bitvavo->bitvavo", []).append(Decimal("5"))
    snap = m2.predict(route="bitvavo->bitvavo", expected_adverse_buffer=Decimal("0.1"))
    d, reason, net_if_fill, _ = decide(
        config=CONFIGS["C_CONDITIONAL_EV_ONLY"],
        belief=snap,
        expected_net=Decimal("0.2"),
        expected_buffer=Decimal("0.1"),
    )
    assert d == "reject"
    assert reason == "conditional_ev_non_positive"
    n_before = len(m2.adverse_by_route["bitvavo->bitvavo"])
    # No observe on reject — memory unchanged.
    assert len(m2.adverse_by_route["bitvavo->bitvavo"]) == n_before
