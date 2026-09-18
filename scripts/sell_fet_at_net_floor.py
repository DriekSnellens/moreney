#!/usr/bin/env python3
"""Sell the open FET desk holding once unrealized net falls to a EUR floor.

Polls /live/momentum/status and POSTs /live/momentum/sell when net ≤ floor.
Patient (maker) sell by default. Exits cleanly if FET is already flat.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

STATUS_URL = "http://127.0.0.1:8020/live/momentum/status"
SELL_URL = "http://127.0.0.1:8020/live/momentum/sell"
BASE = "FET"
NET_FLOOR_EUR = 300.0
POLL_SEC = 2.0
URGENT = False


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _post(url: str) -> dict:
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        # HTML redirect body is fine; treat 2xx/3xx as ok via urlopen
        body = resp.read().decode(errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": True, "http": getattr(resp, "status", None), "body": body[:200]}


def _log(msg: str) -> None:
    print(f"{datetime.now(UTC).isoformat()} {msg}", flush=True)


def main() -> int:
    floor = float(sys.argv[1]) if len(sys.argv) > 1 else NET_FLOOR_EUR
    _log(f"watch {BASE} sell when unrealized_net_eur <= {floor:.2f}")
    while True:
        try:
            status = _get(STATUS_URL)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            _log(f"status error: {exc}")
            time.sleep(POLL_SEC)
            continue
        pos = next(
            (
                p
                for p in (status.get("positions") or [])
                if str(p.get("base") or "").upper() == BASE
                and float(p.get("quantity") or 0) > 1e-12
            ),
            None,
        )
        if pos is None:
            _log(f"{BASE} not open; done")
            return 0
        net = float(pos.get("unrealized_net_eur") or 0.0)
        mark = pos.get("mark")
        hid = pos.get("holding_id")
        _log(f"net={net:.2f} mark={mark} peak={pos.get('peak_return')} id={hid}")
        if net <= floor:
            url = f"{SELL_URL}?holding_id={hid}"
            if URGENT:
                url += "&urgent=1"
            _log(f"floor hit ({net:.2f} <= {floor:.2f}); selling {hid}")
            try:
                out = _post(url)
                _log(f"sell response: {out}")
            except Exception as exc:  # noqa: BLE001
                _log(f"sell failed: {exc}")
                return 2
            # Wait until flat or timeout.
            for _ in range(90):
                time.sleep(2.0)
                try:
                    st = _get(STATUS_URL)
                except Exception:  # noqa: BLE001
                    continue
                still = any(
                    str(p.get("base") or "").upper() == BASE
                    and float(p.get("quantity") or 0) > 1e-12
                    for p in (st.get("positions") or [])
                )
                me = st.get("manual_exit") or {}
                _log(f"waiting flat still={still} manual_exit={me}")
                if not still and me.get("done", True):
                    _log("flat; done")
                    return 0
            _log("sell submitted but still open after wait")
            return 1
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
