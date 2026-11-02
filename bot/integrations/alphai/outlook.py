"""AlphaI tone for the operator dashboard.

The banner is the seven-day news tone on BTC. Each held coin gets the same
tone. The recommended list is the live daily-pick score, highest first.
Neither number is a price forecast. The live book is not switched by this view.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.integrations.alphai.client import AlphaIClient
from bot.integrations.alphai.daily_recommendations import (
    load_daily_recommendations,
    maybe_refresh_daily,
)
from bot.integrations.alphai.regime import _resolve_api_key
from bot.integrations.alphai.symbols import alphai_crypto_ticker
from bot.live.momentum_desk import DEFAULT_UNIVERSE

OUTLOOK_PATH = Path("data/alphai/outlook.json")
_MAX_AGE_SEC = 15 * 60
_VERSION = 3
_AMSTERDAM = ZoneInfo("Europe/Amsterdam")
_MONTHS = (
    "jan",
    "feb",
    "mrt",
    "apr",
    "mei",
    "jun",
    "jul",
    "aug",
    "sep",
    "okt",
    "nov",
    "dec",
)

_STYLE = """
.alphai-outlook { margin: .85rem 0 1rem; padding: .9rem 1rem 1rem;
  border: 1px solid var(--border, #1e293b); border-radius: 14px;
  background: rgba(15,23,42,.72); }
.alphai-outlook h2 { margin: 0; font-size: 1rem; }
.alphai-outlook .sub { margin: .15rem 0 .7rem; color: var(--muted, #94a3b8); font-size: .78rem; }
.outlook-banner { display: flex; align-items: center; justify-content: space-between;
  gap: .8rem; padding: .75rem .9rem; border-radius: 12px; margin-bottom: .8rem; }
.outlook-banner strong { font-size: 1.55rem; letter-spacing: .04em; }
.outlook-banner p { margin: .15rem 0 0; font-size: .78rem; opacity: .9; }
.tone-bear { background: rgba(248,113,113,.14); color: #fecaca; }
.tone-bull { background: rgba(52,211,153,.14); color: #a7f3d0; }
.tone-neutral, .tone-unknown { background: rgba(148,163,184,.12); color: #e2e8f0; }
.outlook-cols { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1.3fr); gap: .8rem; }
.outlook-cols h3 { margin: 0 0 .35rem; font-size: .72rem; letter-spacing: .06em;
  text-transform: uppercase; color: var(--muted, #94a3b8); }
.outlook-row, .outlook-pick { display: flex; align-items: baseline; gap: .45rem;
  padding: .28rem 0; border-bottom: 1px solid rgba(148,163,184,.15); font-size: .88rem; }
.outlook-row b, .outlook-pick b { font-family: ui-monospace, monospace; min-width: 3.2rem; }
.outlook-tag { margin-left: auto; font-size: .72rem; font-weight: 700; letter-spacing: .04em; }
.outlook-pick .rank { color: var(--muted, #94a3b8); width: 1.2rem; }
.outlook-badge { font-size: .68rem; padding: .05rem .35rem; border-radius: 999px;
  background: rgba(52,211,153,.18); color: #a7f3d0; }
.outlook-note { color: var(--muted, #94a3b8); font-size: .72rem; }
@media (max-width: 720px) { .outlook-cols { grid-template-columns: 1fr; } }
"""


def classify_tone(bullish: int, bearish: int) -> str:
    """Bearish when bearish headlines outnumber bullish ones, and the reverse."""
    if bullish <= 0 and bearish <= 0:
        return "neutral"
    if bearish > bullish:
        return "bearish"
    if bullish > bearish:
        return "bullish"
    return "neutral"


def tone_label(tone: str) -> str:
    return {"bearish": "Bearish", "bullish": "Bullish", "neutral": "Neutraal"}.get(
        tone, "Onbekend"
    )


def holding_bases(*statuses: Mapping[str, Any] | None) -> list[str]:
    """Bases with a live quantity. Cash and empty rows are left out."""
    found: list[str] = []
    for status in statuses:
        if not status:
            continue
        for pos in status.get("positions") or []:
            if not isinstance(pos, dict):
                continue
            try:
                qty = float(pos.get("quantity") or pos.get("notional_eur") or 0.0)
            except (TypeError, ValueError):
                continue
            if qty <= 1e-12:
                continue
            base = str(pos.get("base") or "").upper()
            if base and base not in found and base != "EUR":
                found.append(base)
    return found


def _counts(row: Mapping[str, Any] | None) -> tuple[int, int, int]:
    if not row:
        return 0, 0, 0
    return (
        int(row.get("bullish") or 0),
        int(row.get("bearish") or 0),
        int(row.get("neutral") or 0),
    )


def _tone_row(base: str, raw: Mapping[str, Any] | None) -> dict[str, Any]:
    bull, bear, neutral = _counts(raw)
    missing = raw is None or bool((raw or {}).get("missing"))
    tone = "unknown" if missing else classify_tone(bull, bear)
    return {
        "base": base,
        "tone": tone,
        "label": "geen nieuws" if missing else tone_label(tone),
        "bullish": bull,
        "bearish": bear,
        "neutral": neutral,
    }


def _amsterdam_label(raw: str | None) -> str:
    if not raw:
        return "—"
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(_AMSTERDAM)
    month = _MONTHS[local.month - 1]
    return f"{local.day} {month} {local.year}, {local:%H:%M} Amsterdam"


def build_outlook(
    *,
    sentiment: Mapping[str, Mapping[str, Any]],
    report: Mapping[str, Any] | None,
    portfolio: Sequence[str],
    fetched_at: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Assemble the dashboard view. Picks stay in score order, highest first."""
    market = _tone_row("BTC", sentiment.get("BTC"))
    held = [_tone_row(base, sentiment.get(base)) for base in portfolio]
    buy_bases: set[str] = set()
    scored: dict[str, dict[str, Any]] = {}
    for bucket, recommended in (("picks", True), ("watch", False), ("avoid", False)):
        for pick in (report or {}).get(bucket) or []:
            if not isinstance(pick, dict):
                continue
            base = str(pick.get("base") or "").upper()
            if not base:
                continue
            try:
                score = float(pick.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if recommended:
                buy_bases.add(base)
            current = scored.get(base)
            if current is not None and float(current["score"]) >= score:
                continue
            tone = _tone_row(base, sentiment.get(base))
            scored[base] = {
                "base": base,
                "score": score,
                "note": str(pick.get("note") or "")[:90],
                "tone": tone["tone"],
                "label": tone["label"],
                "bullish": tone["bullish"],
                "bearish": tone["bearish"],
                "recommended": base in buy_bases,
            }
    for row in scored.values():
        row["recommended"] = row["base"] in buy_bases
    ranked = sorted(scored.values(), key=lambda row: (-float(row["score"]), row["base"]))
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    picks = [row for row in ranked if row["recommended"]]
    avoid = [
        str(row.get("base") or "").upper()
        for row in ((report or {}).get("avoid") or [])
        if isinstance(row, dict) and row.get("base")
    ]
    return {
        "market": market,
        "portfolio": held,
        "picks": picks,
        "ranked": ranked,
        "avoid": avoid,
        "fetched_at": fetched_at,
        "fetched_label": _amsterdam_label(fetched_at),
        "error": error,
        "session_id": (report or {}).get("session_id"),
    }


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _fresh(cached: Mapping[str, Any], *, now: datetime, portfolio: Sequence[str]) -> bool:
    fetched = str(cached.get("fetched_at") or "")
    text = fetched.replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    age = (now - stamp.astimezone(UTC)).total_seconds()
    if int(cached.get("version") or 0) != _VERSION:
        return False
    if age < 0 or age > _MAX_AGE_SEC:
        return False
    sentiment = cached.get("sentiment") or {}
    if not isinstance(sentiment, dict):
        return False
    return all(base in sentiment for base in portfolio)


def _save(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def refresh_outlook(
    settings: Any,
    portfolio: Sequence[str],
    *,
    path: Path = OUTLOOK_PATH,
    client: AlphaIClient | None = None,
    now: datetime | None = None,
    allow_network: bool | None = None,
) -> dict[str, Any]:
    """Return a cached view, or rebuild it. Tests skip the network."""
    instant = now or datetime.now(UTC)
    held = [str(base).upper() for base in portfolio if str(base).strip()]
    cached = _read_cache(path)
    if cached and _fresh(cached, now=instant, portfolio=held):
        view = cached.get("view")
        if isinstance(view, dict):
            return view
    network = allow_network
    if network is None:
        network = "PYTEST_CURRENT_TEST" not in os.environ
    report_path = str(
        getattr(settings, "alphai_daily_recommendations_path", None)
        or "data/alphai/daily_recommendations.json"
    )
    report = load_daily_recommendations(report_path)
    sentiment: dict[str, Any] = {}
    if isinstance(cached, dict) and isinstance(cached.get("sentiment"), dict):
        sentiment = dict(cached["sentiment"])
    error: str | None = None
    if network:
        key = _resolve_api_key(settings)
        own_client = client
        if own_client is None and key:
            own_client = AlphaIClient(key, timeout_sec=8.0)
        if own_client is not None:
            try:
                report = maybe_refresh_daily(
                    own_client,
                    report_path,
                    focus_bases=set(DEFAULT_UNIVERSE) | set(held),
                    enabled=True,
                    min_relevance=int(
                        getattr(settings, "alphai_daily_recommendations_min_relevance", 6) or 6
                    ),
                    top_n=int(getattr(settings, "alphai_daily_recommendations_top_n", 8) or 8),
                    update_hour_local=int(
                        getattr(settings, "alphai_daily_recommendations_hour", 12) or 12
                    ),
                    interval_minutes=int(
                        getattr(settings, "alphai_recommendations_interval_minutes", 15) or 15
                    ),
                    force=False,
                    now=instant,
                ) or report
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:180]
            want = ["BTC", *held]
            for bucket in ("picks", "watch", "avoid"):
                for pick in (report or {}).get(bucket) or []:
                    if isinstance(pick, dict) and pick.get("base"):
                        want.append(str(pick["base"]).upper())
            seen: list[str] = []
            for base in want:
                if base and base not in seen:
                    seen.append(base)
            for base in seen[:24]:
                try:
                    sentiment[base] = own_client.sentiment_summary(alphai_crypto_ticker(base))
                except Exception:  # noqa: BLE001
                    sentiment.setdefault(base, {"missing": True, "bullish": 0, "bearish": 0})
        elif not key:
            error = "geen AlphaI-sleutel"
    fetched_at = instant.astimezone(UTC).isoformat()
    if not network and cached and cached.get("fetched_at"):
        fetched_at = str(cached["fetched_at"])
    view = build_outlook(
        sentiment=sentiment,
        report=report if isinstance(report, dict) else None,
        portfolio=held,
        fetched_at=fetched_at,
        error=error,
    )
    if network:
        _save(
            path,
            {
                "version": _VERSION,
                "fetched_at": fetched_at,
                "sentiment": sentiment,
                "view": view,
            },
        )
    return view


def render_alphai_outlook(view: Mapping[str, Any] | None) -> str:
    """Dutch panel: market tone, held coins, and the bullish ranking."""
    if not view:
        return ""
    market = view.get("market") if isinstance(view.get("market"), dict) else {}
    tone = str(market.get("tone") or "unknown")
    css = "bear" if tone == "bearish" else "bull" if tone == "bullish" else "neutral"
    counts = (
        f"{int(market.get('bullish') or 0)} bullish · "
        f"{int(market.get('bearish') or 0)} bearish · 7 dagen nieuws"
    )
    held = view.get("portfolio") if isinstance(view.get("portfolio"), list) else []
    if held:
        held_html = "".join(
            "<div class='outlook-row'>"
            f"<b>{escape(str(row.get('base') or ''))}</b>"
            f"<span>{int(row.get('bullish') or 0)} / {int(row.get('bearish') or 0)}</span>"
            f"<span class='outlook-tag'>{escape(str(row.get('label') or ''))}</span>"
            "</div>"
            for row in held
            if isinstance(row, dict)
        )
    else:
        held_html = (
            "<p class='outlook-note'>Geen munt in de portefeuille. Het boek staat in cash.</p>"
        )
    ranked = view.get("ranked")
    if not isinstance(ranked, list):
        ranked = view.get("picks") if isinstance(view.get("picks"), list) else []
    if ranked:
        bits: list[str] = []
        if not any(isinstance(row, dict) and row.get("recommended") for row in ranked):
            bits.append(
                "<p class='outlook-note'>AlphaI raadt nu geen munt aan als bullish. "
                "Hoogste score eerst.</p>"
            )
        for row in ranked:
            if not isinstance(row, dict):
                continue
            rank = int(row.get("rank") or 0)
            score = float(row.get("score") or 0.0)
            if rank == 1 and row.get("recommended"):
                badge = "<span class='outlook-badge'>meest bullish</span>"
            elif rank == 1:
                badge = "<span class='outlook-note'>hoogste score</span>"
            else:
                badge = ""
            bits.append(
                "<div class='outlook-pick'>"
                f"<span class='rank'>{rank}</span>"
                f"<b>{escape(str(row.get('base') or ''))}</b>"
                f"<span>score {score:.0f}</span>"
                f"{badge}"
                f"<span class='outlook-note'>{escape(str(row.get('label') or ''))}</span>"
                "</div>"
            )
        pick_html = "".join(bits)
    else:
        pick_html = "<p class='outlook-note'>AlphaI noemt nu geen munt als bullish.</p>"
    avoid = [str(base) for base in (view.get("avoid") or []) if base]
    avoid_html = (
        f"<p class='outlook-note'>Vermijden: {escape(', '.join(avoid))}</p>" if avoid else ""
    )
    error = str(view.get("error") or "")
    error_html = (
        "<p class='outlook-note'>Laatste telling, verversen lukte niet "
        f"({escape(error[:120])}).</p>"
        if error
        else ""
    )
    return (
        f"<section class='alphai-outlook' id='alphai-outlook' data-tone='{escape(tone)}'>"
        f"<style>{_STYLE}</style>"
        "<h2>AlphaI</h2>"
        "<p class='sub'>Bullish of bearish volgens het nieuws, "
        "en welke munt AlphaI het hoogst scoort.</p>"
        f"<div class='outlook-banner tone-{css}'>"
        f"<div><strong>{escape(tone_label(tone))}</strong>"
        f"<p>{escape(counts)}</p></div>"
        f"<p>bijgewerkt {escape(str(view.get('fetched_label') or '—'))}</p>"
        "</div>"
        f"{error_html}"
        "<div class='outlook-cols'>"
        "<div><h3>In de portefeuille</h3>"
        f"{held_html}</div>"
        "<div><h3>Aangeraden, meest bullish eerst</h3>"
        f"{pick_html}{avoid_html}</div>"
        "</div></section>"
    )
