"""Pull the AlphaI crypto news archive and append a forward snapshot.

The archive is ``GET /api/news/`` with ``category=crypto`` over the plan's
history window. The forward ledger stores today's radar and seven-day
sentiment summary. Neither of those endpoints serves a historical series.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.integrations.alphai.client import AlphaIClient
from bot.integrations.alphai.parse import parse_news_page
from bot.live.momentum_desk import DEFAULT_UNIVERSE

ARCHIVE_PATH = Path("data/research/alphai_crypto_news.json")
LEDGER_PATH = Path("data/research/alphai_forward_ledger.jsonl")
# Leave headroom for the live desk's own polls.
RESERVE_CALLS = 800
# Pro archive horizon documented by AlphaI. A 403 shortens this.
HORIZON_DAYS = (180, 90, 30)


def _next_cursor(page: dict[str, Any]) -> str | None:
    raw = page.get("next_cursor")
    if isinstance(raw, str) and raw:
        return raw
    return None


def compact_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for headline in parse_news_page(page):
        out.append(
            {
                "uid": headline.uid,
                "published": headline.published_at,
                "relevance": headline.relevance,
                "category": headline.category,
                "sentiments": dict(headline.sentiments),
                "title": headline.title[:80],
            }
        )
    return out


def _budget(client: AlphaIClient, *, cap: int) -> int:
    remaining = client.last_rate_limit.remaining
    if remaining is None:
        return min(cap, 40)
    return max(0, min(cap, remaining - RESERVE_CALLS))


def page_crypto_news(
    client: AlphaIClient,
    *,
    from_date: str,
    to_date: str,
    max_calls: int,
    page_size: int = 50,
    log: Any = print,
) -> dict[str, Any]:
    """Walk one published-date window. ``complete`` is false when the cursor remains."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor: str | None = None
    calls = 0
    error: str | None = None
    size = page_size
    while calls < max_calls:
        try:
            page = client.list_news(
                category="crypto",
                min_relevance=6,
                page_size=size,
                cursor=cursor,
                from_date=from_date,
                to_date=to_date,
                collapse_story=True,
            )
        except RuntimeError as exc:
            message = str(exc)
            if calls == 0 and size > 20 and "HTTP 400" in message:
                size = 20
                continue
            error = message[:300]
            break
        calls += 1
        if calls == 1:
            log(f"  first page keys {sorted(page.keys())} results {len(page.get('results') or [])}")
        batch = compact_page(page)
        for row in batch:
            uid = str(row["uid"])
            if uid in seen:
                continue
            seen.add(uid)
            rows.append(row)
        cursor = _next_cursor(page)
        if calls == 1 or calls % 25 == 0:
            log(
                f"  page {calls} rows {len(rows)} "
                f"remaining {client.last_rate_limit.remaining} cursor={bool(cursor)}"
            )
        if not cursor or not batch:
            cursor = None
            break
        time.sleep(0.05)
    return {
        "from_date": from_date,
        "to_date": to_date,
        "calls": calls,
        "rows": rows,
        "complete": error is None and cursor is None and calls > 0,
        "error": error,
        "page_size": size,
    }


def _inside_horizon(today: str, error: str) -> str | None:
    """The boundary date itself is refused. Step one day inside the plan window."""
    import re

    match = re.search(r'"archive_days":\s*(\d+)', error)
    if not match:
        return None
    days = int(match.group(1))
    start = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=max(1, days - 1))
    return start.strftime("%Y-%m-%d")


def fetch_archive(
    client: AlphaIClient,
    *,
    today: str | None = None,
    max_calls: int = 700,
    log: Any = print,
) -> dict[str, Any]:
    """Page the crypto archive back to the plan horizon. A 403 steps inside it."""
    end = today or datetime.now(UTC).strftime("%Y-%m-%d")
    # One cheap call so the rate-limit headers exist before the budget is set.
    client.list_symbols()
    budget = _budget(client, cap=max_calls)
    log(f"archive budget {budget} calls, remaining {client.last_rate_limit.remaining}")
    if budget < 5:
        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "from_date": None,
            "to_date": end,
            "calls": 1,
            "rows": [],
            "complete": False,
            "error": "rate-limit reserve left no budget for the archive",
            "rate_limit_remaining": client.last_rate_limit.remaining,
        }
    last: dict[str, Any] | None = None
    starts = [
        (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=horizon)).strftime("%Y-%m-%d")
        for horizon in HORIZON_DAYS
    ]
    tried: set[str] = set()
    while starts:
        start = starts.pop(0)
        if start in tried or start > end:
            continue
        tried.add(start)
        log(f"trying {start} → {end}")
        last = page_crypto_news(
            client,
            from_date=start,
            to_date=end,
            max_calls=budget,
            log=log,
        )
        error = str(last.get("error") or "")
        if "HTTP 403" in error:
            inside = _inside_horizon(end, error)
            log(f"  horizon refused at {start}, inside={inside}")
            if inside and inside not in tried:
                starts.insert(0, inside)
            continue
        break
    assert last is not None
    published = [
        str(row.get("published") or "")
        for row in last["rows"]
        if row.get("published")
    ]
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "from_date": last["from_date"],
        "to_date": last["to_date"],
        "calls": int(last["calls"]) + 1,
        "complete": bool(last["complete"]),
        "error": last.get("error"),
        "page_size": last.get("page_size"),
        "n_rows": len(last["rows"]),
        "first_published": min(published) if published else None,
        "last_published": max(published) if published else None,
        "rate_limit_remaining": client.last_rate_limit.remaining,
        "rows": last["rows"],
    }
    return payload


def save_archive(payload: dict[str, Any], path: Path = ARCHIVE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_archive(path: Path = ARCHIVE_PATH) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"unexpected archive at {path}")
    return raw


def _scalars(row: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            kept[key] = value
    return kept


def _compact_radar(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") or payload.get("tickers") or []
    rows: list[dict[str, Any]] = []
    if isinstance(results, list):
        for row in results:
            if isinstance(row, dict):
                rows.append(_scalars(row))
    return {
        "as_of": payload.get("as_of"),
        "window": payload.get("window"),
        "market": payload.get("market"),
        "n": len(rows),
        "rows": rows[:50],
    }


def record_forward(
    client: AlphaIClient,
    *,
    path: Path = LEDGER_PATH,
    log: Any = print,
) -> dict[str, Any]:
    """Append today's radar and seven-day sentiment. Failures stay in the row."""
    bases = ("BTC", *DEFAULT_UNIVERSE)
    snap: dict[str, Any] = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "radar": {},
        "sentiment": {},
        "errors": [],
    }
    for window in ("24h", "4h"):
        try:
            snap["radar"][window] = _compact_radar(
                client.signals_snapshot(market="crypto", window=window, limit=50)
            )
        except RuntimeError as exc:
            snap["errors"].append(f"radar {window}: {exc}"[:240])
    for base in bases:
        ticker = f"{base}-USD"
        try:
            snap["sentiment"][base] = client.sentiment_summary(ticker)
        except RuntimeError as exc:
            snap["errors"].append(f"sentiment {base}: {exc}"[:240])
    snap["rate_limit_remaining"] = client.last_rate_limit.remaining
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snap) + "\n")
    log(
        f"forward ledger radar "
        f"{ {k: v.get('n') for k, v in snap['radar'].items()} } "
        f"sentiment {len(snap['sentiment'])} errors {len(snap['errors'])}"
    )
    return snap
