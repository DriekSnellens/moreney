"""Manual Bitvavo round-trips that the sleeve ledgers never recorded."""

from __future__ import annotations

from bot.live.external_roundtrip import ledger_exit_net_eur, unbooked_roundtrip_exits


def _cap_history() -> list[dict]:
    return [
        {
            "type": "buy",
            "executedAt": "2026-10-05T15:54:00+00:00",
            "sentCurrency": "EUR",
            "sentAmount": "1996.8",
            "receivedCurrency": "CAP",
            "receivedAmount": "31806.428",
            "priceAmount": "0.06278",
            "feesAmount": "3.2",
            "feesCurrency": "EUR",
        },
        {
            "type": "sell",
            "executedAt": "2026-10-06T05:35:00+00:00",
            "sentCurrency": "CAP",
            "sentAmount": "31806.428",
            "receivedCurrency": "EUR",
            "receivedAmount": "2183.57",
            "priceAmount": "0.068652",
            "feesAmount": "3.49",
            "feesCurrency": "EUR",
        },
    ]


def test_cap_roundtrip_nets_about_180():
    rows = unbooked_roundtrip_exits(_cap_history(), {})
    assert len(rows) == 1
    row = rows[0]
    assert row["base"] == "CAP"
    assert row["reason"] == "manual_external"
    assert row["net_eur"] == 180.08
    assert row["dry_run"] is False
    assert row["ts"].startswith("2026-10-06T05:35:00")


def test_already_booked_sell_is_not_emitted_again():
    rows = unbooked_roundtrip_exits(_cap_history(), {"CAP": 31806.428})
    assert rows == []


def test_history_before_dashboard_reset_is_ignored():
    from datetime import UTC, datetime

    old = {
        "type": "sell",
        "executedAt": "2026-09-25T13:08:00+00:00",
        "sentCurrency": "BTC",
        "sentAmount": "0.01",
        "receivedCurrency": "EUR",
        "receivedAmount": "740",
        "priceAmount": "74000",
        "feesAmount": "1",
        "feesCurrency": "EUR",
    }
    rows = unbooked_roundtrip_exits(
        [*_cap_history(), old],
        {},
        since=datetime(2026, 9, 28, 20, 51, tzinfo=UTC),
    )
    assert [row["base"] for row in rows] == ["CAP"]


def test_sell_without_a_buy_in_view_is_not_invented_profit():
    items = [
        {
            "type": "sell",
            "executedAt": "2026-10-06T05:35:00+00:00",
            "sentCurrency": "BTC",
            "sentAmount": "0.04",
            "receivedCurrency": "EUR",
            "receivedAmount": "3000",
            "priceAmount": "75000",
            "feesAmount": "5",
            "feesCurrency": "EUR",
        }
    ]
    assert unbooked_roundtrip_exits(items, {}) == []


def test_ledger_exit_net_ignores_paper_and_pre_reset(tmp_path):
    from datetime import UTC, datetime

    path = tmp_path / "ledger.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"ts": "2026-09-28T20:51:33+00:00", "event": "dashboard_reset"}',
                '{"ts": "2026-09-20T00:00:00+00:00", "event": "exit", "dry_run": false, "net_eur": 999}',
                '{"ts": "2026-10-05T12:40:32+00:00", "event": "exit", "dry_run": false, "net_eur": 1246.39}',
                '{"ts": "2026-10-06T05:35:52+00:00", "event": "exit", "dry_run": false, "net_eur": 180.08}',
                '{"ts": "2026-10-06T06:00:00+00:00", "event": "exit", "dry_run": true, "net_eur": -50}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    since = datetime(2026, 9, 28, 20, 51, 33, tzinfo=UTC)
    assert ledger_exit_net_eur([path], since=since) == 1426.47


def test_staking_crumb_below_minimum_is_ignored():
    """FET's booked sell left a staking crumb; that is not a new trade."""
    items = [
        {
            "type": "buy",
            "executedAt": "2026-10-02T08:41:00+00:00",
            "sentCurrency": "EUR",
            "sentAmount": "100",
            "receivedCurrency": "FET",
            "receivedAmount": "84922.59",
            "feesAmount": "0",
            "feesCurrency": "EUR",
        },
        {
            "type": "sell",
            "executedAt": "2026-10-05T12:40:00+00:00",
            "sentCurrency": "FET",
            "sentAmount": "84929.06",
            "receivedCurrency": "EUR",
            "receivedAmount": "19189.15",
            "priceAmount": "0.22594",
            "feesAmount": "30.7",
            "feesCurrency": "EUR",
        },
    ]
    rows = unbooked_roundtrip_exits(items, {"FET": 84922.58776375})
    assert rows == []
