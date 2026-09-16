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

# Product retirement: CVD abandoned after TOB shadow expectancy failed.
# See docs/POST_CVD_VELOCITY_DESK.md.
CVD_ABANDONED = True
