"""Minimal AlphaI REST client (https://api.alphai.io)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.alphai.io"


@dataclass(frozen=True)
class AlphaIRateLimit:
    limit: int | None
    remaining: int | None
    reset_epoch: int | None


class AlphaIClient:
    """Sync HTTP client — call from ``asyncio.to_thread`` on the hot path."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 30.0,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ValueError("AlphaI API key is required")
        self._api_key = key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec
        self.last_rate_limit = AlphaIRateLimit(None, None, None)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        qs = f"?{urlencode(params, doseq=True)}" if params else ""
        url = f"{self._base_url}{path}{qs}"
        req = Request(
            url,
            method=method,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                self.last_rate_limit = AlphaIRateLimit(
                    _int_header(resp.headers.get("X-RateLimit-Limit")),
                    _int_header(resp.headers.get("X-RateLimit-Remaining")),
                    _int_header(resp.headers.get("X-RateLimit-Reset")),
                )
                body = resp.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"AlphaI HTTP {exc.code}: {detail}") from exc
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def list_symbols(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/symbols/")
        if not isinstance(data, list):
            raise RuntimeError("unexpected /api/symbols/ payload")
        return data

    def get_symbol(self, ticker: str) -> dict[str, Any] | None:
        try:
            data = self._request("GET", f"/api/symbols/{ticker}/")
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        return data if isinstance(data, dict) else None

    def list_news(
        self,
        *,
        symbol: str | None = None,
        category: str | list[str] | None = None,
        min_relevance: int | None = None,
        page_size: int = 20,
        cursor: str | None = None,
        sort: str | None = None,
        collapse_story: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size}
        if symbol:
            params["symbol"] = symbol
        if category is not None:
            if isinstance(category, list):
                params["category"] = ",".join(category)
            else:
                params["category"] = category
        if min_relevance is not None:
            params["min_relevance"] = min_relevance
        if cursor:
            params["cursor"] = cursor
        if sort:
            params["sort"] = sort
        if collapse_story:
            params["collapse"] = "story"
        data = self._request("GET", "/api/news/", params=params)
        if not isinstance(data, dict):
            raise RuntimeError("unexpected /api/news/ payload")
        return data

    def load_or_fetch_symbol_cache(
        self,
        cache_path: Path,
        *,
        max_age_sec: float = 86400.0,
    ) -> set[str]:
        now = time.time()
        if cache_path.exists():
            try:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    isinstance(raw, dict)
                    and (now - float(raw.get("fetched_at_epoch") or 0)) < max_age_sec
                ):
                    syms = raw.get("crypto_symbols") or raw.get("symbols")
                    if isinstance(syms, list):
                        return {str(s) for s in syms}
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                logger.debug("ALPHAI_SYMBOL_CACHE_READ_FAILED", exc_info=True)
        symbols = self.list_symbols()
        crypto = {
            str(row["symbol"])
            for row in symbols
            if isinstance(row, dict)
            and (
                row.get("asset_type") == "Crypto"
                or str(row.get("symbol", "")).endswith("-USD")
            )
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "fetched_at_epoch": now,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                    "crypto_symbols": sorted(crypto),
                    "total_symbols": len(symbols),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return crypto


def _int_header(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
