"""Tests for research tape retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.market_data.research.retention import (
    effective_retention_days,
    plan_retention,
    prune_research_marketdata,
)


def test_plan_retention_parses_date_partition(tmp_path: Path) -> None:
    old = tmp_path / f"date={(datetime.now(UTC).date() - timedelta(days=10)).isoformat()}"
    recent = tmp_path / f"date={(datetime.now(UTC).date() - timedelta(days=1)).isoformat()}"
    old.mkdir()
    recent.mkdir()
    plan = plan_retention(tmp_path, retention_days=3)
    stale = set(plan["stale_partitions"])
    assert str(old) in stale
    assert str(recent) not in stale


def test_prune_research_marketdata_deletes_stale(tmp_path: Path) -> None:
    old = tmp_path / "date=2020-01-01"
    old.mkdir()
    (old / "events.jsonl").write_text("{}", encoding="utf-8")
    result = prune_research_marketdata(tmp_path, retention_days=3, execute_delete=True)
    assert result["deleted"] == [str(old)]
    assert not old.exists()


def test_effective_retention_days_tightens_under_pressure() -> None:
    assert effective_retention_days(configured_days=7, disk_used_pct=50.0) == 7
    assert effective_retention_days(configured_days=7, disk_used_pct=88.0) == 3
    assert effective_retention_days(configured_days=7, disk_used_pct=95.0) == 1
