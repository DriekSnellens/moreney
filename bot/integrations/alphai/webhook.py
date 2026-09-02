"""AlphaI Pro webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac


def verify_webhook_signature(
    secret: str,
    signature_header: str | None,
    body: bytes,
) -> bool:
    """Verify ``X-Alphai-Signature`` HMAC (hex or ``sha256=`` prefixed)."""
    if not secret or not signature_header:
        return False
    sig = signature_header.strip()
    if sig.startswith("sha256="):
        sig = sig.split("=", 1)[1]
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)
