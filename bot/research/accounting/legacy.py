"""Repository-wide scan for unlabeled legacy accounting fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Explicit allowlist: mapped or deprecated with a canonical comment/name nearby.
_ALLOW_SUBSTRINGS = (
    "MeanEdgeExecutionReplayNetPerFillEUR",
    "mean_edge_execution_replay_net_per_fill",
    "canonical_replay_net_per_fill",
    "RealizedReplayNetPerFillEUR",
    "DEPRECATED",
    "mapped to canonical",
    "observed_realized",
    "net_eur_per_fill",  # paper tracker observed world — see mapping
)

_PATTERNS = ("net_per_fill", "pnl_per_fill", "NET_per_fill", "NET/fill")


def scan_legacy_fields(
    root: Path | str = ".",
    *,
    extra_allow: tuple[str, ...] = (),
) -> dict[str, Any]:
    root_p = Path(root)
    hits: list[dict[str, Any]] = []
    skip_parts = {
        ".venv",
        "venv",
        ".git",
        "node_modules",
        "__pycache__",
        "data",
        ".pytest_cache",
    }
    for path in root_p.rglob("*"):
        if any(part in skip_parts for part in path.parts):
            continue
        if path.suffix not in {".py", ".md", ".html", ".json"}:
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            lower = line
            for pat in _PATTERNS:
                if pat not in lower:
                    continue
                allowed = any(s in line for s in _ALLOW_SUBSTRINGS + extra_allow)
                if allowed:
                    continue
                # Tests that assert the historical failure mode are allowed.
                if "0.00503" in line or "mean_edge" in line or "unlabeled" in line:
                    continue
                hits.append(
                    {
                        "path": str(path),
                        "line": i,
                        "pattern": pat,
                        "text": line.strip()[:240],
                    }
                )
    return {
        "n_hits": len(hits),
        "hits": hits,
        "patterns": list(_PATTERNS),
        "note": (
            "Hits are unlabeled or unmapped legacy fields. "
            "Canonical names and explicit deprecation comments are allowlisted."
        ),
    }
