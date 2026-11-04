"""Match a spot strategy to coin news and macro news, then run the book.

Usage::

    .venv/bin/python -m bot.research.news_strategy_match

The macro archive is cached after the first fetch. The API key stays in the
environment and is never written into the cache.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from bot.integrations.alphai.client import AlphaIClient
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.alphai_sentiment_accuracy.payoff import _ohlc
from bot.research.alphai_sentiment_accuracy.score import side_of
from bot.research.news_strategy_match.engine import (
    STRATEGIES,
    Taken,
    build_episodes,
    choice_counts,
    control_trades,
    deploy_compound,
    deploy_fixed,
    market_side,
    matched_trades,
)

NEWS = Path("data/research/alphai_crypto_news.json")
MACRO = Path("data/research/alphai_macro_news.json")
BARS = Path("data/research/alphai_sleeve/15m")
OUT = Path("bot/research/news_strategy_match/RESULTS.md")
CONTROLS = (
    ("bullish coin, houd 24u", "bull_24h"),
    ("bullish coin, houd 7d", "bull_7d"),
    ("elk coin-nieuws, houd 24u", "news_24h"),
)


def _base(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if text.endswith("-USD"):
        return text[: -len("-USD")]
    return text


def _day(published: str) -> str | None:
    try:
        stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).date().isoformat()


def load_coin_sides(path: Path) -> dict[str, dict[str, list[int]]]:
    raw = json.loads(path.read_text())
    seen: set[tuple[str, str]] = set()
    out: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in raw.get("rows") or []:
        uid = str(row.get("uid") or "")
        day = _day(str(row.get("published") or ""))
        if day is None:
            continue
        for ticker, sentiment in (row.get("sentiments") or {}).items():
            base = _base(str(ticker))
            if not base or (uid, base) in seen:
                continue
            seen.add((uid, base))
            side = side_of(str(sentiment))
            if side is None or side == 0:
                continue
            out[base][day].append(side)
    return {base: dict(days) for base, days in out.items()}


def _api_key() -> str:
    key = os.environ.get("ALPHAI_API_KEY", "").strip()
    if key:
        return key
    env = Path(".env")
    if not env.exists():
        return ""
    for line in env.read_text(errors="replace").splitlines():
        if line.startswith("ALPHAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def fetch_macro(start: str, end: str, path: Path) -> dict[str, object]:
    """Fetch one UTC day at a time so a page cap cannot drop the older headlines.

    A 403 before the first successful day is the archive horizon: that day is
    skipped. Progress is written after every day, so a throttle can resume.
    """
    key = _api_key()
    if not key:
        raise SystemExit("no AlphaI key for the macro archive")
    client = AlphaIClient(key)
    cached = json.loads(path.read_text()) if path.exists() else {}
    rows_by_uid = {
        str(row.get("uid") or ""): row
        for row in (cached.get("rows") or [])
        if isinstance(row, dict) and row.get("uid")
    }
    done = set(cached.get("days_done") or [])
    opened = False
    first_day = ""
    day = start
    while day <= end:
        if day not in done:
            try:
                batch = _page_macro(client, day, day)
            except RuntimeError as exc:
                if "HTTP 403" not in str(exc) or opened:
                    raise
                day = _next_day(day)
                continue
            opened = True
            if not first_day:
                first_day = day
            for row in batch:
                rows_by_uid[str(row["uid"])] = row
            done.add(day)
            _save_macro(path, rows_by_uid, done, first_day or start, end, complete=False)
            print(f"macro {day} {len(batch)}", flush=True)
        elif not first_day:
            first_day = day
            opened = True
        day = _next_day(day)
    saved = _save_macro(path, rows_by_uid, done, first_day or start, end, complete=True)
    return saved


def _save_macro(
    path: Path,
    rows_by_uid: dict[str, object],
    done: set[str],
    start: str,
    end: str,
    *,
    complete: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "from_date": start,
        "to_date": end,
        "complete": complete,
        "days_done": sorted(done),
        "rows": list(rows_by_uid.values()),
        "error": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _next_day(day: str) -> str:
    stamp = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return (stamp + timedelta(days=1)).date().isoformat()


def _page_macro(client: AlphaIClient, start: str, end: str) -> list[dict[str, object]]:
    cursor: str | None = None
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for _page in range(40):
        params: dict[str, object] = {
            "from_date": start,
            "to_date": end,
            "page_size": 20,
            "min_relevance": 4,
        }
        if cursor:
            params["cursor"] = cursor
        page = _request_page(client, params)
        batch = page.get("results") or []
        if not batch:
            return rows
        for row in batch:
            if not isinstance(row, dict):
                continue
            compact = _compact_macro(row)
            uid = str(compact.get("uid") or "")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            rows.append(compact)
        nxt = page.get("next_cursor")
        if not nxt or nxt == cursor:
            return rows
        cursor = str(nxt)
        time.sleep(1.1)
    raise RuntimeError(f"macro day {start} still has pages")


def _request_page(client: AlphaIClient, params: dict[str, object]) -> dict[str, object]:
    """One macro page. A 429 waits out the stated pause and tries again."""
    for _attempt in range(6):
        try:
            page = client._request("GET", "/api/news/macro/", params=params)
        except RuntimeError as exc:
            text = str(exc)
            if "HTTP 429" not in text:
                raise
            found = re.search(r"retry_after_seconds[^0-9]*(\d+)", text)
            time.sleep(int(found.group(1)) + 1 if found else 30)
            continue
        if not isinstance(page, dict):
            raise RuntimeError("unexpected macro page")
        return page
    raise RuntimeError("macro page kept getting throttled")


def _compact_macro(row: dict[str, object]) -> dict[str, object]:
    original = row.get("original") if isinstance(row.get("original"), dict) else {}
    enrichment = row.get("enrichment") if isinstance(row.get("enrichment"), dict) else {}
    insights = enrichment.get("ai_trading_insights") if isinstance(enrichment, dict) else {}
    trading = insights.get("news_trading_value") if isinstance(insights, dict) else {}
    alignment = ""
    if isinstance(trading, dict):
        alignment = str(trading.get("market_sentiment_alignment") or "")
    published = ""
    uid = ""
    if isinstance(original, dict):
        published = str(original.get("time_published") or "")
        uid = str(original.get("uid") or "")
    category = str(enrichment.get("category") or "") if isinstance(enrichment, dict) else ""
    relevance = int(enrichment.get("relevance_score") or 0) if isinstance(enrichment, dict) else 0
    return {
        "uid": uid,
        "published": published,
        "category": category,
        "relevance": relevance,
        "alignment": alignment[:160],
    }


def load_market_sides(path: Path) -> dict[str, list[int]]:
    raw = json.loads(path.read_text())
    out: dict[str, list[int]] = defaultdict(list)
    for row in raw.get("rows") or []:
        day = _day(str(row.get("published") or ""))
        side = market_side(str(row.get("alignment") or ""))
        if day is None or side is None or side == 0:
            continue
        out[day].append(side)
    return dict(out)


def _load_bars(bases: set[str]) -> dict[str, tuple[np.ndarray, ...]]:
    out: dict[str, tuple[np.ndarray, ...]] = {}
    for base in sorted(bases):
        path = BARS / f"{base}.json"
        if path.exists():
            out[base] = _ohlc(path)
    return out


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    return f"{sign}€{abs(value):,.0f}"


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _book_line(name: str, rows: list[Taken]) -> str:
    n_fixed, pnl = deploy_fixed(rows)
    n_comp, end, dd = deploy_compound(rows)
    mean = sum(row.net for row in rows) / len(rows) if rows else 0.0
    return (
        f"| {name} | {len(rows)} | {n_fixed} | {_eur(pnl)} | {_eur(end)} | "
        f"{_pct(dd)} | {n_comp} | {_pct(mean)} |"
    )


def _write(
    path: Path,
    *,
    episodes: int,
    matched: list[Taken],
    controls: list[tuple[str, list[Taken]]],
    macro_from: str,
    macro_to: str,
    macro_rows: int,
    market_days: int,
    coin_days: int,
) -> None:
    counts = choice_counts(matched)
    count_text = ", ".join(f"{name} {counts.get(name, 0)}" for name in STRATEGIES) or "geen"
    lines = [
        "# Strategie zoeken bij coin-nieuws en marktnieuws",
        "",
        "De regel stond vast voordat de tape draaide. Er is daarna geen drempel",
        "of horizon bijgesteld, en er is niets live gezet.",
        "",
        "Per UTC-dag zijn twee feiten bekend op de eerstvolgende middernacht.",
        "Het coin-nieuws is de meerderheid van de directionele labels van die coin",
        "die dag: bull, bear, of stil. Het marktnieuws is de meerderheid van",
        "`market_sentiment_alignment` op de AlphaI-macrofeed (macro, grondstoffen,",
        "geopolitiek) die dag: bull, bear, gemengd bij gelijkspel, of stil.",
        "Een stille coin op een stille markt blijft cash.",
        "",
        "Voor elk ander paar zoekt de bot in de al afgesloten uitkomsten van",
        "datzelfde paar. Het menu is een spot-koop: 24 uur vasthouden, een 10%",
        "trail tot 7 dagen, of 7 dagen vasthouden. Een strategie telt pas bij",
        "minstens 8 afgesloten uitkomsten en een positief gemiddeld nettorendement.",
        "Het hoogste gemiddelde wint. Bij gelijkspel wint de eerdere naam in het",
        "menu. Anders cash. Bitvavo spot kan niet shorten, dus shorten zit niet",
        "in het menu. Kosten zijn 0,25% taker per kant. De vulling is de eerste",
        "15-minuten-opening op of na de middernacht. Het boek heeft vier stukken",
        "van €5.000, en een tweede boek dat elk stuk terugzet op een kwart van",
        "de equity. Een coin die al open staat wordt overgeslagen.",
        "",
        f"Macro-archief {macro_from} t/m {macro_to}, {macro_rows} headlines,",
        f"{market_days} dagen met een directionele marktstem. Coin-labels uit de",
        f"crypto-store, {coin_days} coin-dagen met een directionele stem.",
        "Kandidaten zijn de live desk plus elke coin met een label die dag,",
        f"mits er een 15-minuten-tape is. Episodes met een pad: {episodes}.",
        "",
        (
            "| regel | signalen | gevuld vast | PnL vast | eind herbelegd "
            "| max DD | gevuld herbelegd | gem. net signaal |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        _book_line("matcher", matched),
    ]
    for label, rows in controls:
        lines.append(_book_line(label, rows))
    lines += [
        "",
        f"De matcher nam {len(matched)} signalen: {count_text}.",
        "De vaste regels kopen zonder te kijken welk paar van coin en markt",
        "in het verleden betaalde. Het herbelegde eindbedrag begint op €20.000.",
        "Een positief eurobedrag naast een negatief gemiddeld signaal komt doordat",
        "het boek maar vier stukken vult. Het gemiddelde over alle signalen is",
        "de verwachting van de regel.",
        "",
        "Dit venster is de overlap van de crypto-store en het macro-archief.",
        "Een uitkomst op dit stuk tape zet de regel niet live.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    crypto_meta = json.loads(NEWS.read_text())
    start = str(crypto_meta.get("from_date") or "2026-08-05")
    end = str(crypto_meta.get("to_date") or "2026-11-02")
    cached = json.loads(MACRO.read_text()) if MACRO.exists() else {}
    if not cached.get("complete"):
        fetch_macro(start, end, MACRO)
    macro_raw = json.loads(MACRO.read_text())
    coins = load_coin_sides(NEWS)
    market = load_market_sides(MACRO)
    bases = set(DEFAULT_UNIVERSE) | set(coins)
    bars = _load_bars(bases)
    episodes = build_episodes(coins, market, bars, universe=DEFAULT_UNIVERSE)
    matched = matched_trades(episodes)
    controls = [(label, control_trades(episodes, rule)) for label, rule in CONTROLS]
    coin_days = sum(len(days) for days in coins.values())
    _write(
        OUT,
        episodes=len(episodes),
        matched=matched,
        controls=controls,
        macro_from=str(macro_raw.get("from_date") or ""),
        macro_to=str(macro_raw.get("to_date") or ""),
        macro_rows=len(macro_raw.get("rows") or []),
        market_days=len(market),
        coin_days=coin_days,
    )
    print(OUT.read_text())


if __name__ == "__main__":
    main()
