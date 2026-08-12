"""Tests for database scaffolding."""

from database.base import Base
from database.models import ExecutionRecord, TradeOpportunityRecord
from database.session import get_engine, get_session_factory, reset_engine


def test_orm_models_registered() -> None:
    table_names = set(Base.metadata.tables)
    assert "trade_opportunities" in table_names
    assert "executions" in table_names
    assert "risk_events" in table_names
    assert "orders" in table_names
    assert "fills" in table_names
    assert "portfolio_snapshots" in table_names
    assert "daily_statistics" in table_names
    assert "strategy_statistics" in table_names
    assert "exchange_pair_statistics" in table_names
    assert "hourly_statistics" in table_names


def test_models_have_no_withdrawal_tables() -> None:
    names = {name.lower() for name in Base.metadata.tables}
    assert "withdrawals" not in names
    assert "transfers" not in names


def test_model_columns_exist() -> None:
    from database.models import RiskEventRecord

    assert TradeOpportunityRecord.__tablename__ == "trade_opportunities"
    assert ExecutionRecord.__tablename__ == "executions"
    assert RiskEventRecord.__tablename__ == "risk_events"
    assert "symbol" in TradeOpportunityRecord.__table__.c
    assert "opportunity_id" in ExecutionRecord.__table__.c
    assert "kill_switch_state" in RiskEventRecord.__table__.c


def test_session_factory_reset(settings, monkeypatch) -> None:
    reset_engine()
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    engine = get_engine(settings)
    factory = get_session_factory(settings)
    assert engine is not None
    assert factory is not None
    reset_engine()
