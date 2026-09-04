"""AlphaI pick outcomes scorecard — learn whether bullish picks actually win.

Records each recommendation window's picks, settles later with day returns vs BTC.
Descriptive only; does not auto-apply score changes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_OPERATOR_TZ = ZoneInfo("Europe/Amsterdam")
DayReturnFetcher = Callable[[list[str]], dict[str, float]]


def fetch_bitvavo_day_returns(
    bases: list[str],
    *,
    asof: datetime | None = None,
    timeout_sec: float = 8.0,
) -> dict[str, float]:
    """Amsterdam-midnight → now % change on Bitvavo ``BASE-EUR`` markets."""
    instant = asof or datetime.now(UTC)
    local = instant.astimezone(_OPERATOR_TZ)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(day_start.astimezone(UTC).timestamp() * 1000)
    out: dict[str, float] = {}
    for raw in bases:
        base = str(raw or "").strip().upper()
        if not base:
            continue
        market = f"{base}-EUR"
        url = (
            f"https://api.bitvavo.com/v2/{market}/candles"
            f"?interval=1h&start={start_ms}"
        )
        try:
            req = Request(url, headers={"User-Agent": "moreney-alphai-learn"})
            with urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
                candles = json.loads(resp.read().decode())
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("ALPHAI_DAY_RETURN_FETCH_FAIL base=%s err=%s", base, exc)
            continue
        if not isinstance(candles, list) or not candles:
            continue
        try:
            ordered = sorted(candles, key=lambda c: c[0])
            open_px = float(ordered[0][1])
            close_px = float(ordered[-1][4])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if open_px <= 0:
            continue
        out[base] = (close_px / open_px - 1.0) * 100.0
    return out


@dataclass
class PickOutcomeStore:
    """Persisted pick→outcome ledger (cap ~500 sessions)."""

    sessions: list[dict[str, Any]] = field(default_factory=list)
    auto_apply: bool = False

    def record_session(
        self,
        report: Mapping[str, Any],
        *,
        day_returns_pct: Mapping[str, float] | None = None,
        settle: bool = True,
    ) -> dict[str, Any]:
        """Upsert a recommendation session; optionally settle with current returns."""
        session_id = str(report.get("session_id") or "").strip()
        if not session_id:
            session_id = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")
        picks_raw = report.get("picks") or []
        picks: list[dict[str, Any]] = []
        for row in picks_raw:
            if not isinstance(row, dict):
                continue
            base = str(row.get("base") or "").strip().upper()
            if not base:
                continue
            try:
                score = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            try:
                rank = int(row.get("rank") or 0)
            except (TypeError, ValueError):
                rank = 0
            picks.append({"base": base, "score": score, "rank": rank})

        entry = {
            "session_id": session_id,
            "generated_at": str(report.get("generated_at") or ""),
            "macro_caution": bool(report.get("macro_caution")),
            "picks": picks,
            "recorded_at": datetime.now(UTC).isoformat(),
            "settled": False,
            "btc_day_pct": None,
            "outcomes": [],
            "lesson": None,
        }
        # Replace same session_id if present.
        self.sessions = [s for s in self.sessions if s.get("session_id") != session_id]
        self.sessions.append(entry)
        if len(self.sessions) > 500:
            self.sessions = self.sessions[-500:]
        if settle and day_returns_pct:
            self.settle_session(session_id, day_returns_pct)
        return entry

    def settle_session(
        self,
        session_id: str,
        day_returns_pct: Mapping[str, float],
        *,
        btc_base: str = "BTC",
    ) -> dict[str, Any] | None:
        entry = next((s for s in self.sessions if s.get("session_id") == session_id), None)
        if entry is None:
            return None
        btc_key = str(btc_base or "BTC").upper()
        btc_ret = None
        if btc_key in day_returns_pct:
            try:
                btc_ret = float(day_returns_pct[btc_key])
            except (TypeError, ValueError):
                btc_ret = None
        outcomes: list[dict[str, Any]] = []
        beats = 0
        lags = 0
        for pick in entry.get("picks") or []:
            base = str(pick.get("base") or "").upper()
            if base not in day_returns_pct:
                continue
            try:
                ret = float(day_returns_pct[base])
            except (TypeError, ValueError):
                continue
            vs_btc = None if btc_ret is None else ret - btc_ret
            beat = vs_btc is not None and vs_btc > 0
            lag = vs_btc is not None and vs_btc <= -1.5
            if beat:
                beats += 1
            if lag:
                lags += 1
            outcomes.append(
                {
                    "base": base,
                    "rank": pick.get("rank"),
                    "score": pick.get("score"),
                    "day_pct": round(ret, 4),
                    "vs_btc_pp": None if vs_btc is None else round(vs_btc, 4),
                    "beat_btc": beat,
                    "lagging": lag,
                }
            )
        entry["btc_day_pct"] = None if btc_ret is None else round(btc_ret, 4)
        entry["outcomes"] = outcomes
        entry["settled"] = bool(outcomes)
        entry["settled_at"] = datetime.now(UTC).isoformat()
        # Compact lesson string for dashboards / logs.
        if outcomes and btc_ret is not None:
            avg_excess = sum(float(o["vs_btc_pp"] or 0) for o in outcomes) / len(outcomes)
            top = outcomes[0]
            entry["lesson"] = (
                f"picks_avg_vs_btc={avg_excess:+.2f}pp "
                f"beat={beats}/{len(outcomes)} lag={lags}/{len(outcomes)} "
                f"rank1={top.get('base')} {float(top.get('vs_btc_pp') or 0):+.2f}pp"
            )
        return entry

    def settle_open(
        self,
        day_returns_pct: Mapping[str, float],
        *,
        max_age_hours: float = 36.0,
        btc_base: str = "BTC",
    ) -> int:
        """Re-settle recent sessions (including already settled — tape moves)."""
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        n = 0
        for entry in self.sessions:
            gen = str(entry.get("generated_at") or entry.get("recorded_at") or "")
            try:
                ts = datetime.fromisoformat(gen.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
            except ValueError:
                ts = datetime.now(UTC)
            if ts.astimezone(UTC) < cutoff:
                continue
            sid = str(entry.get("session_id") or "")
            if not sid:
                continue
            self.settle_session(sid, day_returns_pct, btc_base=btc_base)
            n += 1
        return n

    def summary(self, *, last_n: int = 48) -> dict[str, Any]:
        recent = [s for s in self.sessions if s.get("settled")][-last_n:]
        rows: list[dict[str, Any]] = []
        for s in recent:
            rows.extend(s.get("outcomes") or [])
        if not rows:
            return {
                "settled_sessions": 0,
                "pick_rows": 0,
                "beat_btc_rate": None,
                "lag_rate": None,
                "avg_vs_btc_pp": None,
                "rank1_lag_rate": None,
                "auto_apply": self.auto_apply,
                "latest_lesson": None,
            }
        beats = sum(1 for r in rows if r.get("beat_btc"))
        lags = sum(1 for r in rows if r.get("lagging"))
        excesses = [float(r["vs_btc_pp"]) for r in rows if r.get("vs_btc_pp") is not None]
        rank1 = [r for r in rows if int(r.get("rank") or 0) == 1]
        rank1_lags = sum(1 for r in rank1 if r.get("lagging"))
        latest = recent[-1] if recent else None
        return {
            "settled_sessions": len(recent),
            "pick_rows": len(rows),
            "beat_btc_rate": round(beats / len(rows), 3),
            "lag_rate": round(lags / len(rows), 3),
            "avg_vs_btc_pp": round(sum(excesses) / len(excesses), 3) if excesses else None,
            "rank1_lag_rate": (
                round(rank1_lags / len(rank1), 3) if rank1 else None
            ),
            "auto_apply": self.auto_apply,
            "latest_lesson": (latest or {}).get("lesson"),
            "latest_session_id": (latest or {}).get("session_id"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_apply": self.auto_apply,
            "sessions": list(self.sessions),
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> PickOutcomeStore:
        store = cls()
        if not isinstance(raw, dict):
            return store
        store.auto_apply = bool(raw.get("auto_apply") or False)
        sessions = raw.get("sessions")
        if isinstance(sessions, list):
            store.sessions = [s for s in sessions if isinstance(s, dict)]
        return store

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path | str) -> PickOutcomeStore:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return cls()


def sync_pick_outcomes(
    report: Mapping[str, Any] | None,
    path: Path | str,
    *,
    day_returns_pct: Mapping[str, float] | None = None,
    fetch_returns: DayReturnFetcher | None = None,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Record + settle picks; return summary or None when disabled/empty."""
    if not enabled or not isinstance(report, dict):
        return None
    picks = [
        str(p.get("base") or "").upper()
        for p in (report.get("picks") or [])
        if isinstance(p, dict) and p.get("base")
    ]
    bases = sorted(set(picks) | {"BTC"})
    returns: dict[str, float] = dict(day_returns_pct or {})
    if fetch_returns is not None:
        try:
            returns.update(fetch_returns(bases))
        except Exception:  # noqa: BLE001
            logger.exception("ALPHAI_PICK_OUTCOMES_FETCH_FAILED")
    store = PickOutcomeStore.load(path)
    store.record_session(report, day_returns_pct=returns or None, settle=bool(returns))
    if returns:
        store.settle_open(returns)
    store.save(path)
    summary = store.summary()
    if summary.get("latest_lesson"):
        logger.info(
            "ALPHAI_PICK_LESSON session=%s %s",
            summary.get("latest_session_id"),
            summary.get("latest_lesson"),
        )
    return summary
