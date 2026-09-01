"""Capital reservation TTL tests."""

from __future__ import annotations

import time
from decimal import Decimal

from bot.intelligence.dynamic_capital_allocator import CapitalReservationStore


def test_concurrent_reservations_expire() -> None:
    store = CapitalReservationStore()
    store.reserve(symbol="A", venue="bitvavo", amount_eur=Decimal("500"), ttl_seconds=60.0)
    assert store.reserved_total() == Decimal("500")
    store.reserve(symbol="B", venue="okx", amount_eur=Decimal("300"), ttl_seconds=0.01)
    time.sleep(0.02)
    assert store.reserved_total() == Decimal("500")


def test_release_frees_capital() -> None:
    store = CapitalReservationStore()
    rid = store.reserve(symbol="A", venue="bitvavo", amount_eur=Decimal("200"), ttl_seconds=60.0)
    store.release(rid)
    assert store.reserved_total() == Decimal("0")
