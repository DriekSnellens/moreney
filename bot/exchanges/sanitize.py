"""Secret-safe logging helpers for the exchange layer."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEYS = frozenset(
    {
        "apiKey",
        "api_key",
        "secret",
        "apiSecret",
        "api_secret",
        "password",
        "passphrase",
        "privateKey",
        "private_key",
        "uid",
        "token",
        "access_token",
        "refresh_token",
        "Authorization",
        "authorization",
        "X-MBX-APIKEY",
    }
)

_REDACTED = "***REDACTED***"
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|passphrase|password|secret|token|authorization)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def redact_value(key: str, value: Any) -> Any:
    """Redact a value when the key looks credential-related."""
    if key in _SECRET_KEYS or _SECRET_PATTERN.search(key):
        return _REDACTED
    return value


def redact_mapping(data: dict[str, Any] | None) -> dict[str, Any]:
    """Return a shallow copy with credential fields redacted."""
    if not data:
        return {}
    return {str(k): redact_value(str(k), v) for k, v in data.items()}


def redact_text(message: str) -> str:
    """Best-effort redaction of secret-looking assignments in free text."""
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", message)


def safe_exc_message(exc: BaseException) -> str:
    """Exception message with obvious secrets stripped."""
    return redact_text(str(exc))
