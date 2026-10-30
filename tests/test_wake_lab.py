"""Wake detector: decline, dormant shelf, then the first interest day."""

from __future__ import annotations

from bot.research.wake_lab.detect import (
    WakeSpec,
    detect_volume_pops,
    detect_wakes,
    forward_audit,
    open_setups,
)

DAY = 86_400_000
T0 = 1_704_067_200_000  # 2024-01-01 UTC


def _rows(closes: list[float], vols: list[float]) -> list[list[float]]:
    rows: list[list[float]] = []
    for i, (close, vol) in enumerate(zip(closes, vols, strict=True)):
        prev = closes[i - 1] if i else close
        rows.append(
            [
                T0 + i * DAY,
                prev,
                max(prev, close) * 1.01,
                min(prev, close) * 0.99,
                close,
                vol,
            ]
        )
    return rows


def _shape() -> list[list[float]]:
    """Fall from 20 to 5, sleep at 4, then one +30% day on much higher volume."""
    decline = [20.0 - 15.0 * i / 39.0 for i in range(40)]
    shelf = [4.0] * 24
    spark = [5.2]
    after = [5.2, 6.4, 8.4, 8.0]
    closes = decline + shelf + spark + after
    vols = [5_000.0] * 40 + [200.0] * 24 + [8_000.0] + [8_000.0] * 4
    return _rows(closes, vols)


def test_wake_fires_on_the_interest_day_after_a_decline_and_a_shelf() -> None:
    rows = _shape()
    sigs = detect_wakes(rows)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig["idx"] == 64
    assert sig["day"] == "2024-03-05"
    assert abs(sig["day_ret"] - 0.30) < 1e-9
    assert sig["drawdown"] > 0.40
    assert sig["vol_mult"] > 3.0
    assert 0.0 < sig["likeness"] <= 1.0


def test_a_vertical_day_after_the_wake_is_not_a_second_signal() -> None:
    closes = [20.0 - 15.0 * i / 39.0 for i in range(40)] + [4.0] * 24 + [5.2] + [5.2] * 10 + [11.0]
    vols = [5_000.0] * 40 + [200.0] * 24 + [8_000.0] + [200.0] * 10 + [20_000.0]
    sigs = detect_wakes(_rows(closes, vols), WakeSpec(cooldown=0))
    assert [s["idx"] for s in sigs] == [64]


def test_no_signal_without_a_prior_decline() -> None:
    closes = [4.0] * 70 + [5.2]
    vols = [200.0] * 70 + [8_000.0]
    assert detect_wakes(_rows(closes, vols)) == []
    pops = detect_volume_pops(_rows(closes, vols))
    assert len(pops) == 1 and pops[0]["idx"] == 70


def test_no_signal_when_the_shelf_is_choppy() -> None:
    decline = [20.0 - 15.0 * i / 39.0 for i in range(40)]
    chop = [4.0 if i % 2 == 0 else 4.8 for i in range(24)]
    closes = decline + chop + [6.2]
    vols = [5_000.0] * 40 + [200.0] * 24 + [8_000.0]
    assert detect_wakes(_rows(closes, vols)) == []


def test_young_history_still_counts_when_the_decline_is_on_the_tape() -> None:
    # Shorter than the 90-day lookback, longer than the 30-day minimum.
    decline = [20.0 - 15.0 * i / 34.0 for i in range(35)]
    closes = decline + [4.0] * 24 + [5.2]
    vols = [5_000.0] * 35 + [200.0] * 24 + [8_000.0]
    sigs = detect_wakes(_rows(closes, vols))
    assert len(sigs) == 1
    assert sigs[0]["drawdown"] >= 0.40


def test_forward_audit_enters_the_next_open_and_counts_the_boom() -> None:
    rows = _shape()
    sigs = [{"base": "AAA", **s} for s in detect_wakes(rows)]
    audit = forward_audit({"AAA": rows}, sigs, horizons=(3,))
    assert audit["n_used"] == 1
    # Next open is the spark close 5.2; the 3-day high is 8.4 * 1.01.
    assert abs(audit["horizons"][3]["high"]["mean"] - (8.4 * 1.01 / 5.2 - 1.0)) < 1e-9
    assert audit["horizons"][3]["high"]["p_ge_boom"] == 1.0


def test_open_setups_drop_a_name_that_already_spiked() -> None:
    rows = _shape()
    sigs = [{"base": "AAA", **s} for s in detect_wakes(rows)]
    assert open_setups({"AAA": rows}, sigs, last_day="2024-03-05") == []
    flat = [20.0 - 15.0 * i / 39.0 for i in range(40)] + [4.0] * 24 + [5.2]
    quiet = _rows(flat, [5_000.0] * 40 + [200.0] * 24 + [8_000.0])
    fresh = [{"base": "BBB", **s} for s in detect_wakes(quiet)]
    still = open_setups({"BBB": quiet}, fresh, last_day=fresh[0]["day"])
    assert len(still) == 1 and still[0]["base"] == "BBB"
