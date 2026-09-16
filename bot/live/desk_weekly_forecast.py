"""Weekly desk forecast for the momentum dashboard.

Builds a Dutch operator brief from AlphaI daily recommendations + Bitvavo
tape + last desk regime. Refreshed each morning (Europe/Amsterdam) while the
live API is up — no trading behaviour changes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_NL = ZoneInfo("Europe/Amsterdam")
_DEFAULT_PATH = Path("./data/desk_weekly_forecast.json")
_BITVAVO_24H = "https://api.bitvavo.com/v2/ticker/24h?market={market}"

_loop_task: asyncio.Task[None] | None = None


def forecast_path(settings: Any | None = None) -> Path:
    raw = None
    if settings is not None:
        raw = getattr(settings, "desk_weekly_forecast_path", None)
    return Path(str(raw or _DEFAULT_PATH))


def load_forecast(path: Path | str | None = None) -> dict[str, Any] | None:
    p = Path(path) if path else _DEFAULT_PATH
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("weekly forecast: read failed %s: %s", p, exc)
        return None
    return raw if isinstance(raw, dict) else None


def save_forecast(path: Path | str, report: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def _local_now(now: datetime | None = None) -> datetime:
    dt = now or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_NL)


def _week_window(local: datetime) -> tuple[str, str, str]:
    monday = (local.date() - timedelta(days=local.weekday())).isoformat()
    sunday = (local.date() + timedelta(days=(6 - local.weekday()))).isoformat()
    return monday, sunday, f"{monday} → {sunday}"


def _fetch_bitvavo_24h(base: str, *, timeout: float = 6.0) -> dict[str, Any] | None:
    url = _BITVAVO_24H.format(market=f"{base.upper()}-EUR")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("weekly forecast: Bitvavo %s failed: %s", base, exc)
        return None
    try:
        last = float(data.get("last") or 0)
        open_ = float(data.get("open") or 0)
        high = float(data.get("high") or 0)
        low = float(data.get("low") or 0)
    except (TypeError, ValueError):
        return None
    if last <= 0 or open_ <= 0:
        return None
    return {
        "base": base.upper(),
        "last": round(last, 2),
        "open": round(open_, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "ret_24h": round(last / open_ - 1.0, 4),
    }


def _desk_regime_snapshot() -> dict[str, Any]:
    path = Path("./data/momentum_desk_state.json")
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    reg = state.get("last_regime") if isinstance(state, dict) else None
    return dict(reg) if isinstance(reg, dict) else {}


def _alphai_bundle(settings: Any) -> dict[str, Any]:
    import os

    from bot.integrations.alphai.daily_recommendations import (
        load_daily_recommendations,
        maybe_refresh_daily,
    )
    from bot.integrations.alphai.symbols import LIQUID_EUR_BASES

    path = str(
        getattr(settings, "alphai_daily_recommendations_path", None)
        or "data/alphai/daily_recommendations.json"
    )
    try:
        key = getattr(settings, "alphai_api_key", None)
        secret = (
            key.get_secret_value()
            if key is not None and hasattr(key, "get_secret_value")
            else (os.environ.get("ALPHAI_API_KEY") or "")
        )
        if secret and bool(getattr(settings, "alphai_daily_recommendations_enabled", True)):
            from bot.integrations.alphai.client import AlphaIClient
            from bot.integrations.alphai.regime import _parse_csv_bases as parse_bases

            focus = parse_bases(
                getattr(settings, "live_micro_focus_bases", "") or "",
                fallback=set(LIQUID_EUR_BASES),
            )
            client = AlphaIClient(str(secret))
            maybe_refresh_daily(
                client,
                path,
                focus_bases=focus,
                enabled=True,
                min_relevance=int(
                    getattr(settings, "alphai_daily_recommendations_min_relevance", 6)
                    or 6
                ),
                top_n=int(
                    getattr(settings, "alphai_daily_recommendations_top_n", 8) or 8
                ),
                update_hour_local=int(
                    getattr(settings, "alphai_daily_recommendations_hour", 12) or 12
                ),
                interval_minutes=int(
                    getattr(settings, "alphai_recommendations_interval_minutes", 15)
                    or 15
                ),
                force=False,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly forecast: AlphaI refresh skipped: %s", exc)
    daily = load_daily_recommendations(path) or {}
    picks = [
        {"base": p.get("base"), "score": p.get("score"), "note": p.get("note")}
        for p in (daily.get("picks") or [])[:5]
        if isinstance(p, dict)
    ]
    avoid = [
        {"base": a.get("base"), "score": a.get("score"), "note": a.get("note")}
        for a in (daily.get("avoid") or [])[:6]
        if isinstance(a, dict)
    ]
    headlines: list[str] = []
    for row in list(daily.get("avoid") or []) + list(daily.get("picks") or []):
        if not isinstance(row, dict):
            continue
        for h in list(row.get("bearish_headlines") or [])[:2]:
            if h and h not in headlines:
                headlines.append(str(h)[:140])
        for h in list(row.get("bullish_headlines") or [])[:1]:
            if h and h not in headlines:
                headlines.append(str(h)[:140])
        if len(headlines) >= 6:
            break
    return {
        "macro_caution": bool(daily.get("macro_caution")),
        "generated_at": daily.get("generated_at"),
        "session_id": daily.get("session_id"),
        "headline_count": daily.get("headline_count"),
        "picks": picks,
        "avoid": avoid,
        "headlines": headlines[:6],
    }


def _bias_from_signals(
    *,
    btc_ret: float | None,
    breadth_hint: float | None,
    macro_caution: bool,
    regime_label: str,
    reasons: list[str],
) -> tuple[str, str]:
    """Return (bias_key, bias_nl)."""
    weak = (
        regime_label in {"weak", "soft"}
        or "weak_tape_idle" in reasons
        or "btc_weak" in reasons
        or "breadth_weak" in reasons
        or (btc_ret is not None and btc_ret < -0.01)
        or (breadth_hint is not None and breadth_hint < 0.4)
    )
    if macro_caution and weak:
        return "cautious", "Voorzichtig / risk-off"
    if weak:
        return "cautious", "Voorzichtig"
    if macro_caution:
        return "mixed", "Gemengd · macro-caution"
    if btc_ret is not None and btc_ret > 0.015 and (breadth_hint or 0) >= 0.55:
        return "constructive", "Constructief"
    return "neutral", "Neutraal / chop"


def _build_narrative(
    *,
    week_label: str,
    bias_nl: str,
    bias_key: str,
    btc: dict[str, Any] | None,
    eth: dict[str, Any] | None,
    regime: dict[str, Any],
    alphai: dict[str, Any],
) -> dict[str, Any]:
    btc_last = btc.get("last") if btc else None
    btc_ret = btc.get("ret_24h") if btc else None
    eth_ret = eth.get("ret_24h") if eth else None
    reasons = [str(r) for r in (regime.get("reasons") or [])]
    regime_label = str(regime.get("regime_label") or "—")
    breadth = regime.get("breadth")
    picks = [str(p.get("base")) for p in alphai.get("picks") or [] if p.get("base")]
    avoid = [str(a.get("base")) for a in alphai.get("avoid") or [] if a.get("base")]
    macro = bool(alphai.get("macro_caution"))

    why: list[str] = []
    if btc_last is not None and btc_ret is not None:
        why.append(
            f"BTC-EUR ~{btc_last:,.0f} € (24u {btc_ret:+.1%})"
            + (f", ETH 24u {eth_ret:+.1%}" if eth_ret is not None else "")
        )
    why.append(
        f"Desk-regime {regime_label}"
        + (f" · breadth {float(breadth):.0%}" if breadth is not None else "")
        + (f" · {', '.join(reasons[:3])}" if reasons else "")
    )
    if macro:
        why.append("AlphaI macro_caution aan — headlines risk-off / liquidaties / policy")
    if avoid:
        why.append("AlphaI avoid: " + ", ".join(avoid[:5]))
    if picks:
        why.append("AlphaI picks (dun bij caution): " + ", ".join(picks[:4]))
    for h in (alphai.get("headlines") or [])[:3]:
        why.append(f"Nieuws: {h}")

    if bias_key == "cautious":
        week_view = (
            f"Week {week_label}: verwachting is voorzichtig. "
            "Eerst stabilisatie / chop; structurele upside pas als BTC én breadth herstellen. "
            "Desk blijft waarschijnlijk idle tot weak-tape voorbij is."
        )
        scenarios = [
            {
                "name": "Base",
                "prob": "~50%",
                "text": "Chop rond recente range; weinig RS-entries, hold BTC ongeveer vlak tot licht rood/groen.",
            },
            {
                "name": "Dieper risk-off",
                "prob": "~30%",
                "text": "Hawkish macro of verse liquidaties → BTC test lagere steun; full-book clips vermijden tot regime firm is.",
            },
            {
                "name": "Relief bounce",
                "prob": "~20%",
                "text": "Zachte policy-toon / de-escalatie → bounce; eerste desk-kans als breadth weer >~50%.",
            },
        ]
        desk = (
            "€20k×1: geduld. Survival idle is OK. Eén slechte full-book entry kost early-stop ~2%. "
            "Hold-sleeve is de structurele BTC-exposure."
        )
    elif bias_key == "constructive":
        week_view = (
            f"Week {week_label}: tape ziet constructiever. "
            "Ruimte voor RS-entries op sterke excess, nog steeds met early-stop discipline."
        )
        scenarios = [
            {
                "name": "Base",
                "prob": "~45%",
                "text": "Voortzetting risk-on in alts die BTC verslaan; desk kan 1–3 trades draaien.",
            },
            {
                "name": "Fade",
                "prob": "~35%",
                "text": "Bounce faalt → terug naar idle; trail/early-stop moeten winsten beschermen.",
            },
            {
                "name": "Squeeze hoger",
                "prob": "~20%",
                "text": "Breedte blijft hoog; full-book winners mogelijk, DD-risico blijft door clip-grootte.",
            },
        ]
        desk = "Entries alleen op regime-ok + excess; AlphaI avoid respecteren bij sizing."
    else:
        week_view = (
            f"Week {week_label}: gemengd / chop. "
            "Geen sterk directioneel edge — selectief of idle tot signalen alignen."
        )
        scenarios = [
            {
                "name": "Base",
                "prob": "~50%",
                "text": "Range-bound BTC; sporadische RS-trades, lage win-rate-week mogelijk.",
            },
            {
                "name": "Break lager",
                "prob": "~25%",
                "text": "Macro-caution wint → weak idle terug.",
            },
            {
                "name": "Break hoger",
                "prob": "~25%",
                "text": "Breadth herstelt → meer entry-kansen op beslismomenten 7/13/16 UTC.",
            },
        ]
        desk = "Geen forceren. Wacht op firm regime of duidelijke AlphaI+tape overlap."

    return {
        "bias": bias_key,
        "bias_label": bias_nl,
        "week_view": week_view,
        "why": why,
        "scenarios": scenarios,
        "desk_implication": desk,
    }


def generate_weekly_forecast(
    settings: Any | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build and persist a fresh weekly forecast snapshot."""
    from bot.core.config import get_settings

    settings = settings or get_settings()
    local = _local_now(now)
    week_start, week_end, week_label = _week_window(local)
    alphai = _alphai_bundle(settings)
    regime = _desk_regime_snapshot()
    btc = _fetch_bitvavo_24h("BTC")
    eth = _fetch_bitvavo_24h("ETH")
    btc_ret = float(btc["ret_24h"]) if btc else (
        float(regime["btc_ret"]) if regime.get("btc_ret") is not None else None
    )
    breadth = None
    try:
        if regime.get("breadth") is not None:
            breadth = float(regime["breadth"])
    except (TypeError, ValueError):
        breadth = None
    reasons = [str(r) for r in (regime.get("reasons") or [])]
    bias_key, bias_nl = _bias_from_signals(
        btc_ret=btc_ret,
        breadth_hint=breadth,
        macro_caution=bool(alphai.get("macro_caution")),
        regime_label=str(regime.get("regime_label") or ""),
        reasons=reasons,
    )
    narrative = _build_narrative(
        week_label=week_label,
        bias_nl=bias_nl,
        bias_key=bias_key,
        btc=btc,
        eth=eth,
        regime=regime,
        alphai=alphai,
    )
    hour = int(getattr(settings, "desk_weekly_forecast_hour_local", 7) or 7)
    next_morning = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local >= next_morning:
        next_morning += timedelta(days=1)
    report = {
        "ok": True,
        "as_of": datetime.now(UTC).isoformat(),
        "as_of_local": local.isoformat(),
        "timezone": "Europe/Amsterdam",
        "week_start": week_start,
        "week_end": week_end,
        "week_label": week_label,
        "next_morning_refresh_local": next_morning.isoformat(),
        "sources": {
            "alphai_session": alphai.get("session_id"),
            "alphai_generated_at": alphai.get("generated_at"),
            "headline_count": alphai.get("headline_count"),
            "bitvavo": True,
            "desk_regime_at": regime.get("at"),
        },
        "tape": {"btc": btc, "eth": eth},
        "desk_regime": {
            "label": regime.get("regime_label"),
            "btc_ret": regime.get("btc_ret"),
            "breadth": regime.get("breadth"),
            "reasons": reasons[:8],
            "alphai": regime.get("alphai"),
        },
        "alphai": {
            "macro_caution": alphai.get("macro_caution"),
            "picks": alphai.get("picks"),
            "avoid": alphai.get("avoid"),
            "headlines": alphai.get("headlines"),
        },
        **narrative,
        "disclaimer": (
            "Operator-prognose op basis van AlphaI-headlines + Bitvavo-tape + laatste desk-regime. "
            "Geen garantie; geen automatische orderwijziging."
        ),
    }
    path = forecast_path(settings)
    save_forecast(path, report)
    logger.info(
        "weekly forecast saved bias=%s week=%s path=%s",
        bias_key,
        week_label,
        path,
    )
    return report


def forecast_is_stale(
    report: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    hour_local: int = 7,
    max_age_hours: float = 26.0,
) -> bool:
    if not report:
        return True
    local = _local_now(now)
    try:
        as_of = datetime.fromisoformat(str(report.get("as_of_local") or report.get("as_of")))
    except ValueError:
        return True
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=_NL)
    as_of_local = as_of.astimezone(_NL)
    if (local - as_of_local).total_seconds() > max_age_hours * 3600:
        return True
    # New local morning after the configured hour and report is from yesterday.
    if local.hour >= hour_local and as_of_local.date() < local.date():
        return True
    return False


def maybe_refresh_forecast(
    settings: Any | None = None,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    from bot.core.config import get_settings

    settings = settings or get_settings()
    if not bool(getattr(settings, "desk_weekly_forecast_enabled", True)):
        return {"ok": False, "reason": "disabled"}
    path = forecast_path(settings)
    cached = load_forecast(path)
    hour = int(getattr(settings, "desk_weekly_forecast_hour_local", 7) or 7)
    if not force and cached and not forecast_is_stale(cached, now=now, hour_local=hour):
        return {"ok": True, "refreshed": False, "forecast": cached}
    report = generate_weekly_forecast(settings, now=now)
    return {"ok": True, "refreshed": True, "forecast": report}


async def forecast_morning_loop(settings: Any | None = None) -> None:
    """Background loop: refresh shortly after the local morning hour."""
    from bot.core.config import get_settings

    settings = settings or get_settings()
    hour = int(getattr(settings, "desk_weekly_forecast_hour_local", 7) or 7)
    # Kick once at boot if missing/stale.
    try:
        await asyncio.to_thread(maybe_refresh_forecast, settings, force=False)
    except Exception:  # noqa: BLE001
        logger.exception("weekly forecast: initial refresh failed")
    while True:
        local = _local_now()
        target = local.replace(hour=hour, minute=5, second=0, microsecond=0)
        if local >= target:
            target += timedelta(days=1)
        sleep_s = max(30.0, (target - local).total_seconds())
        try:
            await asyncio.sleep(sleep_s)
            await asyncio.to_thread(maybe_refresh_forecast, settings, force=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("weekly forecast: morning refresh failed")
            await asyncio.sleep(300)


def start_forecast_background_refresh(settings: Any | None = None) -> None:
    global _loop_task  # noqa: PLW0603
    if _loop_task is not None and not _loop_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _loop_task = loop.create_task(
        forecast_morning_loop(settings), name="desk-weekly-forecast"
    )
    logger.info("weekly forecast background refresh started")


def stop_forecast_background_refresh() -> None:
    global _loop_task  # noqa: PLW0603
    if _loop_task is not None and not _loop_task.done():
        _loop_task.cancel()
    _loop_task = None
