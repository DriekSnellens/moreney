"""In-memory queue for AlphaI Pro webhook pushes."""

from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any

_lock = Lock()
_queue: deque[dict[str, Any]] = deque(maxlen=64)


def push_webhook_article(article: dict[str, Any]) -> None:
    with _lock:
        _queue.append(article)


def drain_webhook_articles() -> list[dict[str, Any]]:
    with _lock:
        out = list(_queue)
        _queue.clear()
        return out
