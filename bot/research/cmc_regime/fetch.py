"""Download CoinMarketCap into the local cache. The key stays in the environment.

The cache lives under ``data/research/cmc/``, which is gitignored with the
rest of ``data/``. A symbol that maps to several assets keeps the active
CMC id whose euro price tracks the Bitvavo close. The match is identity,
not a trading rule.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from bot.research.week_book.engine import DAY_MS, Coin

ROOT = Path("data/research/cmc")
GLOBAL = ROOT / "global.json"
QUOTES = ROOT / "quotes.json"
API = "https://pro-api.coinmarketcap.com"
START = "2024-01-01"
END = "2026-11-06"
QUOTE_START = "2025-11-05"
MATCH_LO = 0.80
MATCH_HI = 1.25
MIN_OVERLAP = 20


def _ts(text: str) -> int:
    stamp = np.datetime64(text.replace("Z", ""), "ms")
    return int(stamp.astype(np.int64))


def _key() -> str:
    key = os.environ.get("CMC_API_KEY", "").strip()
    if not key:
        raise SystemExit("CMC_API_KEY is not set")
    return key


class _ApiError(RuntimeError):
    pass


def _get(path: str, params: dict[str, str]) -> dict:
    url = API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"X-CMC_PRO_API_KEY": _key(), "Accept": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = json.loads(exc.read().decode())
            status = detail.get("status", {})
            if status.get("error_code") == 1008 and attempt < 3:
                time.sleep(65)
                continue
            message = f"{path} {status.get('error_code')} {status.get('error_message')}"
            raise _ApiError(message) from exc
        status = body.get("status", {})
        if status.get("error_code") == 1008 and attempt < 3:
            time.sleep(65)
            continue
        if status.get("error_code"):
            code = status.get("error_code")
            raise _ApiError(f"{path} {code} {status.get('error_message')}")
        time.sleep(1.25)
        return body
    raise _ApiError(f"{path} rate limit")


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_global() -> None:
    body = _get(
        "/v1/global-metrics/quotes/historical",
        {
            "time_start": START,
            "time_end": END,
            "interval": "daily",
            "convert": "EUR",
            "count": "10000",
        },
    )
    points = []
    for row in body["data"]["quotes"]:
        quote = row["quote"]["EUR"]
        points.append(
            {
                "ts": _ts(row["timestamp"]),
                "mcap": float(quote["total_market_cap"]),
                "volume": float(quote["total_volume_24h"]),
                "btc_dom": float(row["btc_dominance"]),
                "eth_dom": float(row["eth_dominance"]),
                "active": int(row.get("active_cryptocurrencies") or 0),
            }
        )
    ROOT.mkdir(parents=True, exist_ok=True)
    GLOBAL.write_text(json.dumps({"points": points}))
    print(f"global {len(points)} days")


def _map_rows(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []
    try:
        body = _get(
            "/v1/cryptocurrency/map",
            {"symbol": ",".join(symbols), "listing_status": "active"},
        )
    except _ApiError as exc:
        if len(symbols) == 1:
            print(f"skip {symbols[0]}: {exc}")
            return []
        mid = len(symbols) // 2
        return _map_rows(symbols[:mid]) + _map_rows(symbols[mid:])
    return list(body["data"])


def _map(symbols: list[str]) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    for batch in _chunks(symbols, 40):
        for row in _map_rows(batch):
            if row.get("rank") is None or row["symbol"] not in found:
                continue
            found[row["symbol"]].append(
                {"id": int(row["id"]), "rank": int(row["rank"]), "name": row["name"]}
            )
    for rows in found.values():
        rows.sort(key=lambda row: row["rank"])
        del rows[3:]
    return found


def _quote_batch(ids: list[int]) -> dict:
    try:
        return _get(
            "/v2/cryptocurrency/quotes/historical",
            {
                "id": ",".join(str(item) for item in ids),
                "time_start": QUOTE_START,
                "time_end": END,
                "interval": "daily",
                "convert": "EUR",
            },
        )
    except _ApiError as exc:
        if len(ids) == 1:
            print(f"skip id {ids[0]}: {exc}")
            return {"data": {}}
        mid = len(ids) // 2
        left = _quote_batch(ids[:mid])
        right = _quote_batch(ids[mid:])
        merged = {}
        for part in (left, right):
            data = part.get("data") or {}
            if isinstance(data, dict):
                merged.update(data)
        return {"data": merged}


def _quotes(ids: list[int]) -> dict[int, list[list[float]]]:
    out: dict[int, list[list[float]]] = {}
    for batch in _chunks(ids, 8):
        body = _quote_batch(batch)
        data = body["data"]
        if isinstance(data, dict) and "quotes" in data:
            nodes = [data]
        elif isinstance(data, dict):
            nodes = list(data.values())
        else:
            nodes = list(data)
        for node in nodes:
            if isinstance(node, list):
                node = node[0]
            if not isinstance(node, dict) or "id" not in node:
                continue
            points = []
            for row in node.get("quotes") or []:
                quote = row["quote"]["EUR"]
                pct = quote.get("percent_change_7d")
                points.append(
                    [
                        _ts(row["timestamp"]),
                        float(quote["price"]),
                        float(quote.get("market_cap") or 0.0),
                        float(quote.get("volume_24h") or 0.0),
                        float(pct) if pct is not None else None,
                    ]
                )
            out[int(node["id"])] = points
    return out


def _ratio(coin: Coin, points: list[list[float]]) -> float | None:
    ratios: list[float] = []
    for ts, price, _mcap, _vol, _pct in points:
        i = coin.idx.get(int(ts) - DAY_MS)
        if i is None or price <= 0.0:
            continue
        close = float(coin.close[i])
        if close <= 0.0:
            continue
        ratios.append(price / close)
    if len(ratios) < MIN_OVERLAP:
        return None
    ratios.sort()
    return ratios[len(ratios) // 2]


def fetch_quotes(coins: list[Coin]) -> None:
    by_base = {coin.base: coin for coin in coins}
    mapped = _map(sorted(by_base))
    chosen: dict[str, dict] = {}
    pending = {base: rows for base, rows in mapped.items() if rows}
    rank_at = {base: 0 for base in pending}
    while pending:
        wanted: list[tuple[str, int, dict]] = []
        batch_ids: list[int] = []
        seen: set[int] = set()
        for base, rows in list(pending.items()):
            slot = rank_at[base]
            if slot >= len(rows):
                del pending[base]
                continue
            meta = rows[slot]
            cmc_id = int(meta["id"])
            wanted.append((base, cmc_id, meta))
            if cmc_id not in seen:
                seen.add(cmc_id)
                batch_ids.append(cmc_id)
        if not batch_ids:
            break
        series = _quotes(batch_ids)
        for base, cmc_id, meta in wanted:
            if base not in pending:
                continue
            ratio = _ratio(by_base[base], series.get(cmc_id, []))
            if ratio is not None and MATCH_LO <= ratio <= MATCH_HI:
                chosen[base] = {
                    "id": cmc_id,
                    "name": meta["name"],
                    "rank": meta["rank"],
                    "ratio": ratio,
                    "points": series.get(cmc_id, []),
                }
                del pending[base]
            else:
                rank_at[base] += 1
                if rank_at[base] >= len(pending.get(base, [])):
                    pending.pop(base, None)
    ROOT.mkdir(parents=True, exist_ok=True)
    QUOTES.write_text(json.dumps({"names": chosen}))
    print(f"quotes matched {len(chosen)} of {len(by_base)}")


def ensure(coins: list[Coin]) -> None:
    if not GLOBAL.exists():
        fetch_global()
    if not QUOTES.exists():
        fetch_quotes(coins)
