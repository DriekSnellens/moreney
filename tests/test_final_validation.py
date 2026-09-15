"""Final validation: frozen matrix, paired overlays, verdict, no new strategies."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bot.research.execution_realism.config import EXECUTION_REALISM_PRODUCTION_ENABLED
from bot.research.final_validation.breakeven import break_even_from_baseline
from bot.research.final_validation.engine import replay_lines_streaming
from bot.research.final_validation.overlay import CanonicalLine, apply_overlay, deterministic_fill
from bot.research.final_validation.protocol import (
    NEW_STRATEGIES_CREATED,
    SCENARIOS,
    UNIVERSE,
    matrix_fingerprint,
    protocol_hash,
    scenario_config_hash,
)
from bot.research.final_validation.verdict import decide

_ZERO = Decimal("0")
_NOTIONAL = Decimal("100")


def _line(i: int, *, net: str = "3.37", window: str = "W0") -> CanonicalLine:
    n = Decimal(net)
    fees = Decimal("0.35")
    slip = Decimal("0.02")
    adv = Decimal("0.08")
    lat = Decimal("0.02")
    gross = n + fees + slip + adv + lat
    return CanonicalLine(
        signal_id=f"{window}_BTCEUR_{i}",
        window_id=window,
        symbol="BTCEUR" if i % 2 == 0 else "ETHEUR",
        route="okx|bitvavo",
        canonical_net=n,
        gross=gross,
        fees=fees,
        slippage=slip,
        adverse=adv,
        latency=lat,
        inventory=_ZERO,
        forward=0.0384,
    )


def _windows(n0: int = 8, n1: int = 8) -> list[tuple[str, list[CanonicalLine]]]:
    w0 = [_line(i, window="W0") for i in range(n0)]
    w1 = [_line(100 + i, window="W1") for i in range(n1)]
    return [("W0", w0), ("W1", w1)]


def test_frozen_scenario_matrix_fingerprint_stable() -> None:
    a = matrix_fingerprint()
    b = matrix_fingerprint()
    assert a == b
    assert len(a) == 64
    assert len(SCENARIOS) == 5
    ids = [s["scenario_id"] for s in SCENARIOS]
    assert ids == ["BASELINE", "MILD_REALISM", "MODERATE_REALISM", "HARSH_REALISM", "STRESS"]
    assert SCENARIOS[0]["classification"] == "BASELINE"
    assert SCENARIOS[1]["classification"] == "REALISTIC"
    assert SCENARIOS[2]["classification"] == "REALISTIC"
    assert SCENARIOS[3]["classification"] == "STRESS"
    assert SCENARIOS[4]["classification"] == "ADVERSARIAL"


def test_scenario_hash_changes_when_parameters_change() -> None:
    base = dict(SCENARIOS[0])
    h0 = scenario_config_hash(base)
    tweaked = dict(base)
    tweaked["fill_prob"] = 0.5
    assert scenario_config_hash(tweaked) != h0
    assert protocol_hash() == protocol_hash()


def test_baseline_overlay_matches_canonical_net() -> None:
    line = _line(1)
    wf = apply_overlay(line, SCENARIOS[0])
    assert wf.execution_net == line.canonical_net
    assert wf.waterfall_residual() == _ZERO
    assert wf.fill_status.value == "FULL_FILL"


def test_paired_candidate_universe(tmp_path: Path) -> None:
    windows = _windows(6, 6)
    out = replay_lines_streaming(
        windows, list(SCENARIOS), run_dir=tmp_path / "run", dataset_fingerprint="t", tape_fingerprint="t"
    )
    counts = {r["scenario_id"]: r["candidate_count"] for r in out["scenario_results"]}
    assert len(set(counts.values())) == 1
    assert counts["BASELINE"] == 12


def test_deterministic_scenario_replay(tmp_path: Path) -> None:
    windows = _windows(5, 5)
    a = replay_lines_streaming(
        windows, list(SCENARIOS), run_dir=tmp_path / "a", dataset_fingerprint="t", tape_fingerprint="t"
    )
    b = replay_lines_streaming(
        windows, list(SCENARIOS), run_dir=tmp_path / "b", dataset_fingerprint="t", tape_fingerprint="t"
    )
    fa = [r["deterministic_fingerprint"] for r in a["scenario_results"]]
    fb = [r["deterministic_fingerprint"] for r in b["scenario_results"]]
    assert fa == fb


def test_canonical_accounting_identity_on_overlay() -> None:
    line = _line(3)
    for scen in SCENARIOS:
        wf = apply_overlay(line, scen)
        if wf.fill_status.value == "NO_FILL":
            assert wf.execution_net == _ZERO
        else:
            assert wf.waterfall_residual() == _ZERO


def test_break_even_calculation() -> None:
    be = break_even_from_baseline(
        execution_net=Decimal("66096.91"),
        n_signals=19557,
        fee_sum=Decimal("6844.95"),
    )
    assert be["interpolation"] is False
    bps = Decimal(be["extra_adverse_required_to_zero_NET_bps"])
    assert bps > 20
    assert be["fill_rate_required_to_zero_NET"] == "0_sign_invariant_under_uniform_miss"
    assert all(row["positive"] for row in be["fill_rate_observed_grid"] if row["fill_prob"] > 0)


def test_verdict_determinism() -> None:
    def row(sid: str, net: str, *, pos: int = 10, neg: int = 5, share: float = 0.2, audit: str = "PASS") -> dict:
        return {
            "scenario_id": sid,
            "execution_net_eur": net,
            "accounting_identity_status": audit,
            "positive_windows": pos,
            "negative_windows": neg,
            "top_window_share": share,
        }

    results = [
        row("BASELINE", "100"),
        row("MILD_REALISM", "80"),
        row("MODERATE_REALISM", "40"),
        row("HARSH_REALISM", "-1"),
        row("STRESS", "-10"),
    ]
    a = decide(results, n_windows=15)
    b = decide(results, n_windows=15)
    assert a == b
    assert a["FINAL_VALIDATION_VERDICT"] == "PROMISING_BUT_INSUFFICIENT"
    robust = decide(results, n_windows=20)
    assert robust["FINAL_VALIDATION_VERDICT"] == "ROBUST_PAPER_CANDIDATE"
    rejected = decide(
        [row("BASELINE", "-1"), row("MILD_REALISM", "-2"), row("MODERATE_REALISM", "-3"),
         row("HARSH_REALISM", "-4"), row("STRESS", "-5")],
        n_windows=20,
    )
    assert rejected["FINAL_VALIDATION_VERDICT"] == "REJECTED"
    fragile = decide(
        [row("BASELINE", "100"), row("MILD_REALISM", "50"), row("MODERATE_REALISM", "0"),
         row("HARSH_REALISM", "-1"), row("STRESS", "-2")],
        n_windows=20,
    )
    assert fragile["FINAL_VALIDATION_VERDICT"] == "EXECUTION_FRAGILE"


def test_no_automatic_hypothesis_creation() -> None:
    assert NEW_STRATEGIES_CREATED == ()
    assert "cross_venue_dislocation" in UNIVERSE
    assert UNIVERSE["H-0005"]["classification"] == "REJECT_AS_INCREMENTAL_FILTER"
    assert UNIVERSE["H-0007"]["classification"] == "REJECT"
    import bot.research.final_validation.engine as eng

    assert "bot.research.llm" not in eng.__name__
    src = Path("bot/research/final_validation/engine.py").read_text(encoding="utf-8")
    assert "bot.research.llm" not in src


def test_production_execution_remains_disabled() -> None:
    assert EXECUTION_REALISM_PRODUCTION_ENABLED is False


def test_no_ambiguous_net_per_fill_labels(tmp_path: Path) -> None:
    out = replay_lines_streaming(
        _windows(4, 4), list(SCENARIOS), run_dir=tmp_path / "lab", dataset_fingerprint="t", tape_fingerprint="t"
    )
    for r in out["scenario_results"]:
        assert "NET/fill" not in r
        assert "net_per_fill" not in r
        assert "CANONICAL_REPLAY_NET_PER_FILL" in r
        assert "EXECUTION_NET_PER_FILL" in r
        assert r["MEAN_EDGE_EXECUTION_NET_PER_FILL"] is None


def test_unsupported_dimensions_are_explicit() -> None:
    from bot.research.final_validation.protocol import UNSUPPORTED_BY_DATA

    dims = {u["dimension"] for u in UNSUPPORTED_BY_DATA}
    assert "quote_disappearance_after_decision" in dims
    assert all(u["status"] == "UNSUPPORTED_BY_DATA" for u in UNSUPPORTED_BY_DATA)


def test_resume_and_corrupt_recompute(tmp_path: Path) -> None:
    windows = _windows(3, 3)
    run_dir = tmp_path / "res"
    first = replay_lines_streaming(
        windows, list(SCENARIOS), run_dir=run_dir, dataset_fingerprint="t", tape_fingerprint="t"
    )
    assert first["windows_skipped"] == []
    second = replay_lines_streaming(
        windows, list(SCENARIOS), run_dir=run_dir, dataset_fingerprint="t", tape_fingerprint="t", resume=True
    )
    assert second["windows_replayed"] == []
    assert len(second["windows_skipped"]) == 2 * 5
    from bot.research.execution_realism.artifacts import window_artifact_path

    victim = window_artifact_path(run_dir, "W0", "BASELINE")
    victim.write_text("{", encoding="utf-8")
    third = replay_lines_streaming(
        windows, list(SCENARIOS), run_dir=run_dir, dataset_fingerprint="t", tape_fingerprint="t", resume=True
    )
    assert any(k.startswith("W0:BASELINE") for k in third["windows_replayed"])


def test_window_aggregation() -> None:
    line_pos = _line(1, net="5")
    line_neg = _line(2, net="-1")
    wf_pos = apply_overlay(line_pos, SCENARIOS[0])
    wf_neg = apply_overlay(line_neg, SCENARIOS[0])
    assert wf_pos.execution_net > 0
    assert wf_neg.execution_net < 0


def test_deterministic_fill_is_stable() -> None:
    a = [deterministic_fill(f"S{i}", "MILD_REALISM", 0.90) for i in range(40)]
    b = [deterministic_fill(f"S{i}", "MILD_REALISM", 0.90) for i in range(40)]
    assert a == b
    assert all(deterministic_fill(f"S{i}", "BASELINE", 1.0) for i in range(20))
