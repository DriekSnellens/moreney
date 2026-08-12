"""Tests ensuring interfaces and architectural boundaries."""

import inspect

from bot.core.interfaces import ExchangeClient
from bot.exchanges.base import BaseExchangeClient
from bot.strategies import base as strategies_base
from bot.strategies import stub as strategies_stub


def test_exchange_client_has_no_withdraw_methods() -> None:
    forbidden = {"withdraw", "withdrawal", "transfer_out", "send_funds", "cash_out"}
    methods = {name.lower() for name in dir(ExchangeClient)}
    methods |= {name.lower() for name in dir(BaseExchangeClient)}
    assert methods.isdisjoint(forbidden)


def test_strategy_modules_do_not_import_exchanges() -> None:
    from bot.strategies import arbitrage as strategies_arbitrage

    for module in (strategies_base, strategies_stub, strategies_arbitrage):
        source = inspect.getsource(module)
        assert "bot.exchanges" not in source
        assert "ExchangeClient" not in source
