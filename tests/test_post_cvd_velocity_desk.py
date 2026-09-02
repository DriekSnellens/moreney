"""Tests for Capital Velocity Desk (post-CVD retirement)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bot.core.config import Settings
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_engine import LiveMicroEngine
from bot.live.micro_session import _session_settings
from bot.live.production_flags import CVD_ABANDONED
from bot.live.velocity_desk_kills import evaluate_kill_criteria
from bot.portfolio.portfolio import PaperPortfolio


def test_cvd_abandoned_product_flag() -> None:
    assert CVD_ABANDONED is True
    s = Settings()
    assert s.live_cvd_abandoned is True
    assert s.live_micro_ring_util_b_ignore_underwater is True


def test_micro_session_velocity_desk_overrides(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(),
        budget_eur=Decimal("2000"),
        symbols=["ADAEUR"],
        persist_path=tmp_path / "paper.json",
    )
    assert cfg.live_cvd_abandoned is True
    assert cfg.live_disable_research_hooks is True
    assert cfg.live_micro_ring_util_b_ignore_underwater is True
    assert float(cfg.live_micro_ring_momentum_min_return) == 0.0005
    assert int(cfg.live_micro_buy_quality_underwater_count) == 4


def test_ring_soft_eligible_ignores_underwater_when_unlocked(monkeypatch) -> None:
    settings = Settings(
        live_micro_ring_util_b_ignore_underwater=True,
        live_cvd_abandoned=True,
        live_micro_ring_soft_block_underwater_eur=25.0,
        live_micro_ring_soft_max_active_eur=650.0,
        live_micro_active_ring_eur=1000.0,
    )
    ex = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("5000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        execute_venues={"bitvavo", "okx"},
        live_maker=True,
    )
    assert ex._ring_util_b_ignore_underwater is True  # noqa: SLF001
    assert ex._cvd_abandoned is True  # noqa: SLF001

    monkeypatch.setattr(ex, "_ring_needs_deploy", lambda venue: True)
    monkeypatch.setattr(
        ex, "_underwater_book_notional", lambda venue: Decimal("200")
    )
    monkeypatch.setattr(ex, "_active_book_notional", lambda venue: Decimal("0"))
    assert ex._ring_soft_momentum_eligible("bitvavo") is True  # noqa: SLF001

    ex._ring_util_b_ignore_underwater = False  # noqa: SLF001
    assert ex._ring_soft_momentum_eligible("bitvavo") is False  # noqa: SLF001


def test_why_idle_shows_cvd_abandoned() -> None:
    settings = Settings(
        live_micro_ring_util_b_ignore_underwater=True,
        live_cvd_abandoned=True,
    )
    ex = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("5000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        execute_venues={"bitvavo"},
        live_maker=True,
    )
    hints = ex._why_idle_hints()  # noqa: SLF001
    assert any("CVD_ABANDONED" in h for h in hints)
    assert any("UTIL_B_IGNORE_UNDERWATER" in h for h in hints)


def test_kill_criteria_deploy_and_thesis() -> None:
    dead = evaluate_kill_criteria(
        ring_eur=0,
        free_eur=800,
        hours_since_unlock=30,
        days_unlocked=6,
        net_per_hour_eur=0.1,
    )
    assert dead["killed"] is True
    assert dead["cvd_status"] == "ABANDONED"
    assert dead["stage_ok"]["deploy"] is False
    assert dead["stage_ok"]["thesis"] is False

    ok = evaluate_kill_criteria(
        ring_eur=700,
        free_eur=800,
        hours_since_unlock=30,
        fills_per_hour=3,
        sleeve_net_eur=5,
        closed_round_trips=40,
        window_hours=48,
        days_unlocked=6,
        net_per_hour_eur=0.8,
        sleeve_daily_pnl_eur=2,
        weekly_fifo_eur=10,
    )
    assert ok["killed"] is False
    assert all(ok["stage_ok"].values())
