"""Live-only production flags.

Research shadow-validation previously owned PRODUCTION_EXECUTION_ENABLED.
That coupling is removed: live micro is the production path.
"""

from __future__ import annotations

# Live Bitvavo micro sessions are the production execution path.
PRODUCTION_EXECUTION_ENABLED = True

# When True, PaperRunner skips CVD inject / shadow observer / research panels
# (used by live micro so research code is not on the hot path).
LIVE_DISABLE_RESEARCH_HOOKS = True

# Narrow dual-sleeve exception: live_cvd_limited_enabled (Settings) may inject
# frozen CVD into the live GOE path even when LIVE_DISABLE_RESEARCH_HOOKS is True.
# Default remains False until shadow VALIDATED + explicit product flip.
