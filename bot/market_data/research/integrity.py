"""Stream validation / integrity accounting for research tape."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from bot.market_data.research.schema import ResearchMarketEvent


REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "venue",
    "symbol",
    "received_ts_ns",
    "local_monotonic_ns",
)


@dataclass
class IntegrityStats:
    observed: int = 0
    valid: int = 0
    invalid: int = 0
    rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    timestamp_regressions: int = 0
    sequence_gaps: int = 0
    out_of_order: int = 0
    missing_l1: int = 0
    with_depth: int = 0
    crossed: int = 0
    locked: int = 0
    malformed_json: int = 0
    by_venue: dict[str, dict[str, int]] = field(default_factory=dict)

    def _reason(self, code: str) -> None:
        self.reasons[code] = self.reasons.get(code, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed": self.observed,
            "valid": self.valid,
            "invalid": self.invalid,
            "rejected": self.rejected,
            "reasons": dict(self.reasons),
            "duplicates": self.duplicates,
            "timestamp_regressions": self.timestamp_regressions,
            "sequence_gaps": self.sequence_gaps,
            "out_of_order": self.out_of_order,
            "missing_l1": self.missing_l1,
            "with_depth": self.with_depth,
            "crossed": self.crossed,
            "locked": self.locked,
            "malformed_json": self.malformed_json,
            "by_venue": self.by_venue,
        }


def iter_jsonl_raw(root: Path) -> Iterator[tuple[Path, dict[str, Any] | None, str | None]]:
    for path in sorted(root.rglob("*.jsonl")):
        # Support both legacy and session layouts
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    yield path, json.loads(line), None
                except json.JSONDecodeError as exc:
                    yield path, None, str(exc)


def validate_tape(root: Path | str, *, max_events: int | None = None) -> IntegrityStats:
    root = Path(root)
    stats = IntegrityStats()
    seen_ids: set[str] = set()
    last_recv: dict[str, int] = {}
    last_seq: dict[str, int] = {}
    last_ex: dict[str, int] = {}

    for _path, raw, err in iter_jsonl_raw(root):
        if max_events is not None and stats.observed >= max_events:
            break
        stats.observed += 1
        if err is not None or raw is None:
            stats.invalid += 1
            stats.rejected += 1
            stats.malformed_json += 1
            stats._reason("malformed_json")
            continue
        missing = [f for f in REQUIRED_FIELDS if raw.get(f) in (None, "")]
        if missing:
            stats.invalid += 1
            stats.rejected += 1
            stats._reason("missing_" + ",".join(missing[:3]))
            continue
        venue = str(raw.get("venue") or "")
        symbol = str(raw.get("symbol") or "")
        key = f"{venue}|{symbol}"
        bucket = stats.by_venue.setdefault(
            venue,
            {
                "n": 0,
                "exchange_ts": 0,
                "receive_ts": 0,
                "mono_ts": 0,
                "sequence": 0,
                "l1": 0,
                "depth": 0,
            },
        )
        bucket["n"] += 1
        eid = str(raw.get("event_id"))
        if eid in seen_ids:
            stats.duplicates += 1
            stats._reason("duplicate_event_id")
        else:
            seen_ids.add(eid)

        recv = int(raw["received_ts_ns"])
        bucket["receive_ts"] += 1
        if raw.get("local_monotonic_ns") is not None:
            bucket["mono_ts"] += 1
        if raw.get("exchange_ts_ns") is not None:
            bucket["exchange_ts"] += 1
            ex = int(raw["exchange_ts_ns"])
            prev = last_ex.get(key)
            if prev is not None and ex < prev:
                stats.timestamp_regressions += 1
                stats._reason("exchange_ts_regression")
            last_ex[key] = ex
        prev_r = last_recv.get(key)
        if prev_r is not None and recv < prev_r:
            stats.out_of_order += 1
            stats._reason("receive_ts_out_of_order")
        last_recv[key] = recv

        seq = raw.get("sequence_number")
        if seq is not None:
            bucket["sequence"] += 1
            seq_i = int(seq)
            prev_s = last_seq.get(key)
            if prev_s is not None and seq_i > prev_s + 1:
                stats.sequence_gaps += 1
                stats._reason("sequence_gap")
            last_seq[key] = seq_i

        bid = raw.get("bid_price")
        ask = raw.get("ask_price")
        has_l1 = bid not in (None, "", "0", "0.0") or ask not in (None, "", "0", "0.0")
        if has_l1:
            bucket["l1"] += 1
        else:
            stats.missing_l1 += 1
        if raw.get("bid_levels") or raw.get("ask_levels"):
            bucket["depth"] += 1
            stats.with_depth += 1
        if raw.get("crossed_book"):
            stats.crossed += 1
        if raw.get("locked_book"):
            stats.locked += 1

        # Bitvavo must not invent exchange_ts
        if venue == "bitvavo" and raw.get("exchange_ts_ns") is not None and raw.get(
            "exchange_ts_available", False
        ):
            stats.invalid += 1
            stats.rejected += 1
            stats._reason("bitvavo_invented_exchange_ts")
            continue

        stats.valid += 1

    return stats
