"""Web Push when a closed trade's realized P&L is strictly positive.

Uses the existing PWA service worker (``push`` event on ``/live/sw.js``).
Delivery needs VAPID keys, a stored subscription, and the ``pywebpush``
package. Missing pieces are logged; nothing else is notified.

The realized amount is the value already booked by the desk (``net_eur`` /
FIFO ``trade_pnl``). It is not recomputed here.
"""

from __future__ import annotations

import fcntl
import json
import logging
import threading
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROFIT_PUSH_TITLE = "💰 Winst gerealiseerd"
_CENT = Decimal("0.01")
_MAX_NOTIFIED = 5000
_MAX_SUBSCRIPTIONS = 32
_CLOSE_EVENTS = frozenset({"exit", "trim"})

PushSender = Callable[[str, str, Mapping[str, Any]], bool]

_STORE_LOCKS: dict[str, threading.Lock] = {}
_STORE_GUARD = threading.Lock()


def format_eur_nl(amount: Decimal) -> str:
    """Dutch currency amount without the euro sign (``1.234,56``)."""
    quantized = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    sign = "-" if quantized < 0 else ""
    text = f"{abs(quantized):,.2f}"
    text = text.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}{text}"


def profit_message(realized_pnl: Decimal, asset: str) -> tuple[str, str]:
    """Title and body for a realized-profit notification."""
    shown = realized_pnl.quantize(_CENT, rounding=ROUND_HALF_UP)
    label = _asset_label(asset)
    body = f"Je hebt €{format_eur_nl(shown)} winst gerealiseerd met {label}."
    return PROFIT_PUSH_TITLE, body


def ledger_event_id(prefix: str, row: Mapping[str, Any]) -> str:
    """Stable id for one booked close. Retries of the same row share this id."""
    qty = row.get("quantity")
    if qty is None:
        qty = row.get("qty")
    price = row.get("exit_price")
    if price is None:
        price = row.get("price")
    parts = [
        prefix,
        str(row.get("event") or "exit"),
        str(row.get("desk") or row.get("sleeve") or ""),
        str(row.get("holding_id") or ""),
        str(row.get("base") or ""),
        str(qty if qty is not None else ""),
        str(price if price is not None else ""),
        str(row.get("net_eur")),
        str(row.get("reason") or ""),
    ]
    return "|".join(parts)


def note_ledger_close(
    notifier: RealizedProfitNotifier, prefix: str, row: Mapping[str, Any]
) -> None:
    """Queue a push only for a ledger row that books realized P&L on a close."""
    event = str(row.get("event") or "")
    if event not in _CLOSE_EVENTS or "net_eur" not in row:
        return
    try:
        notifier.note(
            event_id=ledger_event_id(prefix, row),
            realized_pnl=row.get("net_eur"),
            asset=str(row.get("base") or ""),
            closed=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("profit push note failed prefix=%s", prefix)


class RealizedProfitNotifier:
    """Hold closes until the state write succeeds, then notify at most once."""

    def __init__(
        self,
        *,
        store_path: str | Path | None = None,
        sender: PushSender | None = None,
    ) -> None:
        self._store_path = Path(store_path) if store_path is not None else None
        self._sender = sender
        self._pending: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def pending_ids(self) -> list[str]:
        with self._lock:
            return [str(item["event_id"]) for item in self._pending]

    def note(
        self,
        *,
        event_id: str,
        realized_pnl: Any,
        asset: str,
        closed: bool = True,
    ) -> None:
        eid = str(event_id or "").strip()
        if not eid:
            return
        with self._lock:
            if any(item["event_id"] == eid for item in self._pending):
                logger.info(
                    "profit push geen notification: duplicate/already notified event=%s",
                    eid,
                )
                return
            self._pending.append(
                {
                    "event_id": eid,
                    "realized_pnl": realized_pnl,
                    "asset": asset,
                    "closed": bool(closed),
                }
            )

    def flush(self, *, persisted: bool) -> list[str]:
        """Send queued closes. A failed save keeps the queue and does not push."""
        try:
            return self._flush(persisted=persisted)
        except Exception:  # noqa: BLE001
            logger.exception("profit push flush failed")
            return ["flush_failed"]

    def _flush(self, *, persisted: bool) -> list[str]:
        with self._lock:
            if not persisted:
                if self._pending:
                    logger.info(
                        "profit push geen notification: opslaan niet gelukt pending=%s",
                        len(self._pending),
                    )
                return ["skipped_not_persisted"] * len(self._pending)
            batch = self._pending
            self._pending = []
        outcomes: list[str] = []
        for item in batch:
            outcomes.append(
                notify_realized_close(
                    event_id=str(item["event_id"]),
                    realized_pnl=item["realized_pnl"],
                    asset=str(item["asset"] or ""),
                    persisted=True,
                    closed=bool(item["closed"]),
                    store_path=self._store_path,
                    sender=self._sender,
                )
            )
        return outcomes


def notify_realized_close(
    *,
    event_id: str,
    realized_pnl: Any,
    asset: str,
    persisted: bool,
    closed: bool = True,
    store_path: str | Path | None = None,
    sender: PushSender | None = None,
) -> str:
    """Send one Web Push for a saved, closed, strictly positive realized P&L."""
    eid = str(event_id or "").strip() or "unknown"
    if not closed:
        logger.info(
            "profit push geen notification: positie niet gesloten "
            "(ongerealiseerde P&L) event=%s",
            eid,
        )
        return "skipped_open"
    if not persisted:
        logger.info(
            "profit push geen notification: opslaan niet gelukt event=%s",
            eid,
        )
        return "skipped_not_persisted"

    shown = _shown_pnl(realized_pnl)
    if shown is None or shown <= 0:
        logger.info(
            "profit push geen notification: realized P&L <= 0 event=%s pnl=%s",
            eid,
            realized_pnl,
        )
        return "skipped_non_positive"

    issues = [] if sender is not None else configuration_issues()
    if issues:
        logger.info(
            "profit push geen notification: web push niet geconfigureerd (%s) event=%s",
            ", ".join(issues),
            eid,
        )
        return "skipped_unconfigured"

    path = _notified_path(store_path)
    if not _claim(path, eid):
        logger.info(
            "profit push geen notification: duplicate/already notified event=%s",
            eid,
        )
        return "skipped_duplicate"

    title, body = profit_message(shown, asset)
    payload = {"title": title, "body": body, "url": "/live/dashboard"}
    deliver = sender or _deliver_web_push
    try:
        ok = bool(deliver(title, body, payload))
    except Exception:  # noqa: BLE001
        logger.exception("profit push send failed event=%s", eid)
        _release(path, eid)
        return "send_failed"
    if not ok:
        logger.info("profit push geen notification: verzenden mislukt event=%s", eid)
        _release(path, eid)
        return "send_failed"
    logger.info(
        "profit push notification verstuurd: realized P&L > 0 event=%s pnl=%s asset=%s",
        eid,
        shown,
        _asset_label(asset),
    )
    return "sent"


def public_push_config() -> dict[str, Any]:
    """Public VAPID key for the existing PWA. Never includes the private key."""
    from bot.core.config import get_settings

    key = (get_settings().web_push_vapid_public_key or "").strip()
    return {"configured": bool(key), "publicKey": key or None}


def configuration_issues() -> list[str]:
    """Human-readable list of what still blocks a real Web Push delivery."""
    from bot.core.config import get_settings

    settings = get_settings()
    missing: list[str] = []
    if not (settings.web_push_vapid_public_key or "").strip():
        missing.append("WEB_PUSH_VAPID_PUBLIC_KEY")
    private = settings.web_push_vapid_private_key
    secret = private.get_secret_value().strip() if private is not None else ""
    if not secret:
        missing.append("WEB_PUSH_VAPID_PRIVATE_KEY")
    if not (settings.web_push_vapid_subject or "").strip():
        missing.append("WEB_PUSH_VAPID_SUBJECT")
    if not _load_subscriptions():
        missing.append("geen opgeslagen push-subscription")
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        missing.append("pywebpush (niet geïnstalleerd)")
    return missing


def store_push_subscription(body: Mapping[str, Any]) -> dict[str, Any]:
    """Persist one PushSubscription from the PWA. Replaces the same endpoint."""
    endpoint = str(body.get("endpoint") or "").strip()
    keys = body.get("keys") if isinstance(body.get("keys"), Mapping) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or len(endpoint) > 2000:
        raise ValueError("push endpoint must be an https URL")
    if not p256dh or not auth:
        raise ValueError("push subscription keys missing")
    row = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
    path = _subscriptions_path()

    def mutate(rows: list[dict[str, Any]]) -> int:
        kept = [item for item in rows if item.get("endpoint") != endpoint]
        kept.append(row)
        if len(kept) > _MAX_SUBSCRIPTIONS:
            del kept[: len(kept) - _MAX_SUBSCRIPTIONS]
        rows[:] = kept
        return len(rows)

    count = _with_json_list(path, mutate)
    logger.info("profit push subscription stored count=%s", count)
    return {"ok": True, "count": count}


def pwa_client_js() -> str:
    """Register the existing service worker and subscribe when VAPID is set."""
    return """
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/live/sw.js').then(function (reg) {
        return subscribeProfitPush(reg);
      }).catch(function () {});
    }

    function urlBase64ToUint8Array(base64String) {
      var padding = '='.repeat((4 - base64String.length % 4) % 4);
      var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      var raw = atob(base64);
      var out = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
      return out;
    }

    async function subscribeProfitPush(reg) {
      try {
        var res = await fetch('/live/push/config', { credentials: 'same-origin' });
        if (!res.ok) return;
        var cfg = await res.json();
        if (!cfg || !cfg.configured || !cfg.publicKey) return;
        if (!('PushManager' in window) || !reg.pushManager) return;
        var perm = Notification.permission;
        if (perm === 'default') perm = await Notification.requestPermission();
        if (perm !== 'granted') return;
        var sub = await reg.pushManager.getSubscription();
        if (!sub) {
          sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(cfg.publicKey)
          });
        }
        await fetch('/live/push/subscribe', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(sub)
        });
      } catch (e) {}
    }
    """


def pwa_client_script() -> str:
    return "<script>" + pwa_client_js() + "</script>"


def _asset_label(asset: str) -> str:
    text = " ".join(str(asset or "").split())
    if not text:
        return "deze positie"
    return text[:80]


def _shown_pnl(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    if not amount.is_finite():
        return None
    try:
        return amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    except Exception:  # noqa: BLE001
        return None


def _notified_path(store_path: str | Path | None) -> Path:
    if store_path is not None:
        return Path(store_path)
    from bot.core.config import get_settings

    return Path(get_settings().web_push_notified_path)


def _subscriptions_path() -> Path:
    from bot.core.config import get_settings

    return Path(get_settings().web_push_subscriptions_path)


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _STORE_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _STORE_LOCKS[key] = lock
        return lock


def _claim(path: Path, event_id: str) -> bool:
    def mutate(ids: list[str]) -> bool:
        if event_id in ids:
            return False
        ids.append(event_id)
        if len(ids) > _MAX_NOTIFIED:
            del ids[: len(ids) - _MAX_NOTIFIED]
        return True

    return bool(_with_json_ids(path, mutate))


def _release(path: Path, event_id: str) -> None:
    def mutate(ids: list[str]) -> None:
        if event_id in ids:
            ids.remove(event_id)

    _with_json_ids(path, mutate)


def _with_json_ids(path: Path, mutate: Callable[[list[str]], Any]) -> Any:
    def runner(raw: list[Any]) -> Any:
        ids = [str(item) for item in raw if item]
        result = mutate(ids)
        raw[:] = ids
        return result

    return _with_json_list(path, runner)


def _with_json_list(path: Path, mutate: Callable[[list[Any]], Any]) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _thread_lock(path), lock_path.open("a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            rows: list[Any] = []
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        rows = loaded
                    elif isinstance(loaded, dict) and isinstance(loaded.get("ids"), list):
                        rows = list(loaded["ids"])
                except (OSError, json.JSONDecodeError):
                    rows = []
            result = mutate(rows)
            payload: Any = {"ids": rows}
            if rows and isinstance(rows[0], dict):
                payload = rows
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
            return result
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _load_subscriptions() -> list[dict[str, Any]]:
    path = _subscriptions_path()
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    out: list[dict[str, Any]] = []
    for item in loaded:
        if isinstance(item, dict) and item.get("endpoint") and isinstance(item.get("keys"), dict):
            out.append(item)
    return out


def _deliver_web_push(title: str, body: str, payload: Mapping[str, Any]) -> bool:
    issues = configuration_issues()
    if issues:
        logger.info(
            "profit push geen notification: web push niet geconfigureerd (%s)",
            ", ".join(issues),
        )
        return False
    from bot.core.config import get_settings

    settings = get_settings()
    private = settings.web_push_vapid_private_key
    assert private is not None
    secret = private.get_secret_value().strip()
    subject = settings.web_push_vapid_subject.strip()
    data = json.dumps(
        {"title": title, "body": body, "url": payload.get("url") or "/live/dashboard"}
    )
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.info("profit push geen notification: pywebpush (niet geïnstalleerd)")
        return False
    delivered = False
    for sub in _load_subscriptions():
        try:
            webpush(
                subscription_info=sub,
                data=data,
                vapid_private_key=secret,
                vapid_claims={"sub": subject},
            )
            delivered = True
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "profit push delivery failed status=%s endpoint=%s",
                status,
                str(sub.get("endpoint") or "")[:80],
            )
            if status in {404, 410}:
                _drop_subscription(str(sub.get("endpoint") or ""))
        except Exception:  # noqa: BLE001
            logger.exception("profit push delivery failed")
    return delivered


def _drop_subscription(endpoint: str) -> None:
    if not endpoint:
        return

    def mutate(rows: list[Any]) -> None:
        rows[:] = [
            item
            for item in rows
            if not (isinstance(item, dict) and item.get("endpoint") == endpoint)
        ]

    try:
        _with_json_list(_subscriptions_path(), mutate)
    except Exception:  # noqa: BLE001
        logger.exception("profit push subscription drop failed")
