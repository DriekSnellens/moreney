"""LIMITED_PAPER_EXECUTION proposal. Written only on SHADOW_VALIDATED.

Never enables PaperExecutor live trading. Never enables production execution.
Requires explicit human approval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.research.shadow_validation.protocol import (
    PROPOSAL_CAPITAL_LIMIT_EUR,
    PROPOSAL_KILL_SWITCH_REQUIRED,
    PROPOSAL_MAX_CONCURRENT_POSITIONS,
    PROPOSAL_MAX_DAILY_LOSS_EUR,
    PROPOSAL_MAX_NOTIONAL_EUR,
    PROPOSAL_PATH,
    PROPOSAL_ROUTE_WHITELIST,
    STRATEGY_ID,
)


def build_proposal() -> dict[str, Any]:
    return {
        "proposal": "LIMITED_PAPER_EXECUTION",
        "strategy_id": STRATEGY_ID,
        "automatically_enabled": False,
        "production_execution": "DISABLED",
        "paper_executor_live_trading": False,
        "requires_explicit_approval": True,
        "capital_limit_eur": PROPOSAL_CAPITAL_LIMIT_EUR,
        "maximum_notional_eur": PROPOSAL_MAX_NOTIONAL_EUR,
        "maximum_concurrent_positions": PROPOSAL_MAX_CONCURRENT_POSITIONS,
        "kill_switch": "REQUIRED" if PROPOSAL_KILL_SWITCH_REQUIRED else "OPTIONAL",
        "maximum_daily_loss_eur": PROPOSAL_MAX_DAILY_LOSS_EUR,
        "route_whitelist": list(PROPOSAL_ROUTE_WHITELIST),
        "monitoring_requirements": [
            "SHADOW VALIDATION dashboard panel must remain visible",
            "Four-world accounting labels must stay unmixed",
            "Kill switch PAUSED / EMERGENCY_STOP blocks new orders",
            "DATA_INVALID rate watched against frozen cap",
            "Daily loss cap enforced by RiskEngine",
        ],
    }


def render_proposal(payload: dict[str, Any]) -> str:
    routes = ", ".join(payload["route_whitelist"])
    monitors = "\n".join(f"- {m}" for m in payload["monitoring_requirements"])
    return f"""# LIMITED_PAPER_EXECUTION proposal

This is a **proposal only**. It does **not** enable production execution.
It does **not** enable PaperExecutor live trading.

Strategy: `{payload["strategy_id"]}`
Route whitelist: `{routes}`

| Limit | Value |
| --- | --- |
| Capital limit | {payload["capital_limit_eur"]} EUR |
| Maximum notional per trade | {payload["maximum_notional_eur"]} EUR |
| Maximum concurrent positions | {payload["maximum_concurrent_positions"]} |
| Kill switch | {payload["kill_switch"]} |
| Maximum daily loss | {payload["maximum_daily_loss_eur"]} EUR |

Automatically enabled: **{payload["automatically_enabled"]}**
Requires explicit approval: **{payload["requires_explicit_approval"]}**
Production execution: **{payload["production_execution"]}**

## Monitoring

{monitors}

Do not expand the route universe. Do not retune dislocation_bps.
H-0005 remains REJECT_AS_INCREMENTAL_FILTER. H-0007 remains REJECT / GATE_INACTIVE.
"""


def maybe_write_proposal(decision: dict[str, Any], *, path: str | Path | None = None) -> bool:
    if decision.get("SHADOW_VALIDATION_VERDICT") != "SHADOW_VALIDATED":
        return False
    if decision.get("NEXT_ACTION") != "PROPOSE_LIMITED_PAPER_EXECUTION":
        return False
    dest = Path(path or PROPOSAL_PATH)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_proposal(build_proposal()), encoding="utf-8")
    return True
