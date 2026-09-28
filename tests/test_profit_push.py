"""Realized-profit Web Push: only a saved close with P&L > 0, once."""

from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bot.live.dashboard import render_live_dashboard
from bot.live.profit_push import (
    PROFIT_PUSH_TITLE,
    RealizedProfitNotifier,
    note_ledger_close,
    notify_realized_close,
    store_push_subscription,
)
from bot.live.pwa_assets import SERVICE_WORKER_JS
from bot.main import app


class _Sender:
    def __init__(self, *, delay: float = 0.0, ok: bool = True) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._delay = delay
        self._ok = ok
        self._lock = threading.Lock()

    def __call__(self, title: str, body: str, payload: dict[str, Any]) -> bool:
        if self._delay:
            time.sleep(self._delay)
        with self._lock:
            self.calls.append((title, body, dict(payload)))
        return self._ok


def _notify(
    tmp_path,
    sender: _Sender,
    *,
    event_id: str = "trade-1",
    realized: object = Decimal("100"),
    asset: str = "BTC",
    persisted: bool = True,
    closed: bool = True,
) -> str:
    return notify_realized_close(
        event_id=event_id,
        realized_pnl=realized,
        asset=asset,
        persisted=persisted,
        closed=closed,
        store_path=tmp_path / "notified.json",
        sender=sender,
    )


def test_closed_plus_100_sends_one_push(tmp_path) -> None:
    sender = _Sender()
    result = _notify(tmp_path, sender, realized=Decimal("100"))
    assert result == "sent"
    assert len(sender.calls) == 1
    title, body, _payload = sender.calls[0]
    assert title == PROFIT_PUSH_TITLE
    assert title == "💰 Winst gerealiseerd"
    assert body == "Je hebt €100,00 winst gerealiseerd met BTC."


def test_closed_plus_one_cent_sends_one_push(tmp_path) -> None:
    sender = _Sender()
    result = _notify(tmp_path, sender, realized=Decimal("0.01"), event_id="cent")
    assert result == "sent"
    assert len(sender.calls) == 1
    assert sender.calls[0][1] == "Je hebt €0,01 winst gerealiseerd met BTC."


def test_closed_zero_sends_nothing(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    sender = _Sender()
    result = _notify(tmp_path, sender, realized=Decimal("0"), event_id="flat")
    assert result == "skipped_non_positive"
    assert sender.calls == []
    assert "geen notification" in caplog.text
    assert "realized P&L <= 0" in caplog.text


def test_closed_loss_sends_nothing(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    sender = _Sender()
    result = _notify(tmp_path, sender, realized=Decimal("-50"), event_id="loss")
    assert result == "skipped_non_positive"
    assert sender.calls == []
    assert "realized P&L <= 0" in caplog.text


def test_same_close_twice_sends_once(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    sender = _Sender()
    first = _notify(tmp_path, sender, event_id="webhook-1", realized=Decimal("100"))
    second = _notify(tmp_path, sender, event_id="webhook-1", realized=Decimal("100"))
    assert first == "sent"
    assert second == "skipped_duplicate"
    assert len(sender.calls) == 1
    assert "duplicate/already notified" in caplog.text


def test_open_unrealized_sends_nothing(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    sender = _Sender()
    result = _notify(
        tmp_path,
        sender,
        event_id="open-1",
        realized=Decimal("100"),
        closed=False,
    )
    assert result == "skipped_open"
    assert sender.calls == []
    assert "niet gesloten" in caplog.text
    notifier = RealizedProfitNotifier(store_path=tmp_path / "notified.json", sender=sender)
    note_ledger_close(
        notifier,
        "momentum",
        {"event": "mark", "base": "ETH", "net_eur": "100", "status": "open"},
    )
    assert notifier.flush(persisted=True) == []
    assert sender.calls == []


def test_failed_save_sends_nothing_until_save_succeeds(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    sender = _Sender()
    direct = _notify(
        tmp_path,
        sender,
        event_id="unsaved",
        realized=Decimal("100"),
        persisted=False,
    )
    assert direct == "skipped_not_persisted"
    assert sender.calls == []
    notifier = RealizedProfitNotifier(store_path=tmp_path / "notified.json", sender=sender)
    notifier.note(event_id="saved-later", realized_pnl=Decimal("100"), asset="SOL")
    assert notifier.flush(persisted=False) == ["skipped_not_persisted"]
    assert sender.calls == []
    assert "opslaan niet gelukt" in caplog.text
    assert notifier.flush(persisted=True) == ["sent"]
    assert len(sender.calls) == 1


def test_concurrent_same_trade_sends_once(tmp_path) -> None:
    sender = _Sender(delay=0.05)
    path = tmp_path / "notified.json"
    barrier = threading.Barrier(8)

    def once() -> None:
        barrier.wait()
        notify_realized_close(
            event_id="race-1",
            realized_pnl=Decimal("100"),
            asset="ADA",
            persisted=True,
            closed=True,
            store_path=path,
            sender=sender,
        )

    threads = [threading.Thread(target=once) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(sender.calls) == 1
    assert sender.calls[0][1] == "Je hebt €100,00 winst gerealiseerd met ADA."


def test_two_notifiers_share_idempotency_store(tmp_path) -> None:
    sender = _Sender()
    path = tmp_path / "notified.json"
    first = RealizedProfitNotifier(store_path=path, sender=sender)
    second = RealizedProfitNotifier(store_path=path, sender=sender)
    row = {
        "event": "exit",
        "holding_id": "h1",
        "base": "NEAR",
        "quantity": "1",
        "exit_price": "2",
        "net_eur": "100",
        "reason": "trail",
    }
    note_ledger_close(first, "momentum", row)
    note_ledger_close(second, "momentum", row)
    assert first.flush(persisted=True) == ["sent"]
    assert second.flush(persisted=True) == ["skipped_duplicate"]
    assert len(sender.calls) == 1


def test_partial_close_uses_booked_net(tmp_path) -> None:
    sender = _Sender()
    notifier = RealizedProfitNotifier(store_path=tmp_path / "notified.json", sender=sender)
    note_ledger_close(
        notifier,
        "clip",
        {
            "event": "trim",
            "holding_id": "lot-1",
            "base": "DOT",
            "quantity": "0.4",
            "exit_price": "3",
            "net_eur": "12.5",
            "reason": "partial_take",
        },
    )
    assert notifier.flush(persisted=True) == ["sent"]
    assert sender.calls[0][1] == "Je hebt €12,50 winst gerealiseerd met DOT."


def test_send_failure_does_not_stick_the_claim(tmp_path) -> None:
    failing = _Sender(ok=False)
    assert _notify(tmp_path, failing, event_id="retry-me") == "send_failed"
    sender = _Sender()
    assert _notify(tmp_path, sender, event_id="retry-me") == "sent"
    assert len(sender.calls) == 1


def test_service_worker_shows_push(tmp_path) -> None:
    del tmp_path
    assert "addEventListener('push'" in SERVICE_WORKER_JS
    assert "Winst gerealiseerd" in SERVICE_WORKER_JS
    assert "showNotification" in SERVICE_WORKER_JS


def test_dashboards_register_existing_worker() -> None:
    html = render_live_dashboard(
        {"session": {"running": True, "bridge": {}}, "observe": {}, "history": []}
    ).body.decode()
    assert "serviceWorker.register('/live/sw.js')" in html
    assert "subscribeProfitPush" in html
    from bot.live.momentum_dashboard import render_momentum_dashboard

    page = render_momentum_dashboard(
        status={"running": False, "holdings": [], "cash_eur": None},
        ledger_rows=[],
    ).body.decode()
    assert "/live/sw.js" in page
    assert "manifest.webmanifest" in page


def test_push_config_and_subscribe_validation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.core.config import get_settings

    monkeypatch.setattr(
        "bot.live.profit_push._subscriptions_path",
        lambda: tmp_path / "subs.json",
    )
    settings = get_settings()
    auth = None
    if settings.dashboard_basic_auth_enabled and settings.dashboard_basic_auth_password is not None:
        auth = (
            settings.dashboard_basic_auth_username,
            settings.dashboard_basic_auth_password.get_secret_value(),
        )
    with TestClient(app) as client:
        cfg = client.get("/live/push/config", auth=auth)
        assert cfg.status_code == 200
        body = cfg.json()
        assert body["configured"] is False
        assert body["publicKey"] is None
        assert "private" not in body
        sw = client.get("/live/sw.js")
        assert "showNotification" in sw.text
        rejected = client.post(
            "/live/push/subscribe",
            auth=auth,
            json={"endpoint": "http://evil.example/push", "keys": {"p256dh": "a", "auth": "b"}},
        )
        assert rejected.status_code == 400
    stored = store_push_subscription(
        {
            "endpoint": "https://push.example/sub",
            "keys": {"p256dh": "pub", "auth": "auth"},
        }
    )
    assert stored["ok"] is True
    again = store_push_subscription(
        {
            "endpoint": "https://push.example/sub",
            "keys": {"p256dh": "pub2", "auth": "auth2"},
        }
    )
    assert again["count"] == 1
