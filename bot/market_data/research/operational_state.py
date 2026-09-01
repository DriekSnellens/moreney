"""Operational recorder / dataset states — do not collapse distinct failures."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ResearchDataState(StrEnum):
    NO_RECORDER = "NO_RECORDER"
    RECORDER_DISABLED = "RECORDER_DISABLED"
    RECORDER_STARTING = "RECORDER_STARTING"
    RECORDING = "RECORDING"
    RECORDING_WITH_DROPS = "RECORDING_WITH_DROPS"
    RECORDER_BROKEN = "RECORDER_BROKEN"
    NO_REAL_TAPE = "NO_REAL_TAPE"
    DATA_NOT_READY = "DATA_NOT_READY"
    DATA_PARTIALLY_READY = "DATA_PARTIALLY_READY"
    DATA_READY_FOR_SLOW_HORIZONS = "DATA_READY_FOR_SLOW_HORIZONS"
    DATA_READY_FOR_FAST_HORIZONS = "DATA_READY_FOR_FAST_HORIZONS"


FINAL_VERDICTS = (
    "NO_REAL_TAPE",
    "RECORDER_DISABLED",
    "RECORDER_BROKEN",
    "DATA_NOT_READY",
    "DATA_PARTIALLY_READY",
    "DATA_READY_FOR_SLOW_HORIZONS",
    "DATA_READY_FOR_FAST_HORIZONS",
)


def resolve_operational_state(
    *,
    recorder_present: bool,
    recorder_enabled: bool,
    recorder_running: bool,
    events_written: int,
    events_dropped: int,
    write_errors: int,
    tape_events: int,
    acceptance_verdict: str | None,
) -> str:
    if not recorder_present:
        return ResearchDataState.NO_RECORDER.value
    if write_errors > 0 and events_written == 0 and recorder_enabled:
        return ResearchDataState.RECORDER_BROKEN.value
    if not recorder_enabled:
        if tape_events <= 0:
            return ResearchDataState.RECORDER_DISABLED.value
        # Historical tape may still exist
    if recorder_enabled and recorder_running and events_written == 0 and tape_events <= 0:
        return ResearchDataState.RECORDER_STARTING.value
    if recorder_enabled and recorder_running:
        if events_dropped > 0:
            return ResearchDataState.RECORDING_WITH_DROPS.value
        if events_written > 0 or tape_events > 0:
            return ResearchDataState.RECORDING.value
    if tape_events <= 0:
        return ResearchDataState.NO_REAL_TAPE.value
    if acceptance_verdict in FINAL_VERDICTS:
        return acceptance_verdict
    return ResearchDataState.DATA_NOT_READY.value


def map_acceptance_to_final(
    *,
    has_tape: bool,
    recorder_enabled: bool,
    write_errors: int,
    events_written_runtime: int,
    slow_ready: bool,
    fast_ready: bool,
    partial: bool,
) -> str:
    if write_errors > 0 and events_written_runtime == 0 and not has_tape:
        return "RECORDER_BROKEN"
    if not has_tape:
        if not recorder_enabled:
            return "RECORDER_DISABLED"
        return "NO_REAL_TAPE"
    if fast_ready:
        return "DATA_READY_FOR_FAST_HORIZONS"
    if slow_ready:
        return "DATA_READY_FOR_SLOW_HORIZONS"
    if partial:
        return "DATA_PARTIALLY_READY"
    return "DATA_NOT_READY"
