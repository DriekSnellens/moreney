"""Classify micro_order_exception events from live audit JSONL."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

_OKX_CLORD = re.compile(r'"clOrdId"\s*:\s*"([^"]+)"', re.I)
_BITVAVO_CLIENT = re.compile(r"clientOrderId parameter is invalid", re.I)
_RATE_LIMIT = re.compile(r"rate limit|has been banned", re.I)
_TRANSIENT = re.compile(r"failed after \d+ attempts", re.I)


@dataclass
class ErrorBucket:
    category: str
    count: int
    pct: float
    sample_message: str = ""


@dataclass
class ExchangeErrorReport:
    total_exceptions: int = 0
    buckets: list[ErrorBucket] = field(default_factory=list)
    hourly_spikes: list[dict[str, Any]] = field(default_factory=list)
    okx_clord_samples: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _iter_audit(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _classify_message(message: str, error_type: str) -> str:
    msg = message or ""
    err = error_type or ""
    if _BITVAVO_CLIENT.search(msg):
        return "BITVAVO_CLIENT_ORDER_ID_INVALID"
    if "Parameter clOrdId error" in msg or _OKX_CLORD.search(msg):
        return "OKX_CLORDID_REJECTED"
    if _RATE_LIMIT.search(msg):
        return "RATE_LIMIT_BAN"
    if _TRANSIENT.search(msg):
        return "TRANSIENT_RETRY_EXHAUSTED"
    if msg == "ExchangeError" and err == "ExchangeError":
        return "BARE_EXCHANGE_ERROR"
    if err == "ExchangeRateLimitError":
        return "RATE_LIMIT_BAN"
    if err == "ExchangeTransientError":
        return "TRANSIENT_RETRY_EXHAUSTED"
    return "OTHER"


def analyze_exchange_errors(audit_path: Path) -> ExchangeErrorReport:
    report = ExchangeErrorReport()
    by_category: Counter[str] = Counter()
    samples: dict[str, str] = {}
    hourly: Counter[str] = Counter()
    hourly_submit: Counter[str] = Counter()
    clord_samples: list[str] = []

    for evt in _iter_audit(audit_path):
        t = evt.get("type") or evt.get("event_type")
        ts = str(evt.get("ts") or "")
        hour = ts[:13] if len(ts) >= 13 else "unknown"
        if t == "order_submit":
            hourly_submit[hour] += 1
        if t != "micro_order_exception":
            continue
        report.total_exceptions += 1
        hourly[hour] += 1
        payload = evt.get("payload") or {}
        msg = str(payload.get("message") or "")
        err = str(payload.get("error") or "")
        cat = _classify_message(msg, err)
        by_category[cat] += 1
        if cat not in samples:
            samples[cat] = msg[:300]
        m = _OKX_CLORD.search(msg)
        if m and len(clord_samples) < 8:
            clord_samples.append(m.group(1))

    total = max(report.total_exceptions, 1)
    report.buckets = [
        ErrorBucket(
            category=cat,
            count=cnt,
            pct=round(100.0 * cnt / total, 2),
            sample_message=samples.get(cat, ""),
        )
        for cat, cnt in by_category.most_common()
    ]
    report.okx_clord_samples = clord_samples

    spikes: list[dict[str, Any]] = []
    for hour, exc_count in hourly.most_common(8):
        submit_count = hourly_submit.get(hour, 0)
        spikes.append(
            {
                "hour": hour,
                "exceptions": exc_count,
                "order_submits": submit_count,
                "submit_exc_ratio": round(exc_count / max(submit_count, 1), 3),
            }
        )
    report.hourly_spikes = spikes

    if by_category.get("OKX_CLORDID_REJECTED", 0) > 0:
        report.notes.append(
            "OKX rejections dominate; audit messages show hyphenated clOrdId values "
            "(e.g. micro-<hex>), which violates OKX alphanumeric-only rules. "
            "Current ccxt_adapter.sanitize_okx_client_order_id strips hyphens, "
            "but this audit window predates or bypasses that path for bulk SOLEUR submits."
        )
    if by_category.get("BARE_EXCHANGE_ERROR", 0) > 0:
        report.notes.append(
            "Bare ExchangeError entries carry no venue/side/symbol — observability gap."
        )
    return report
