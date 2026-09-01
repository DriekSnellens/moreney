"""Research market-data infrastructure (not a trading strategy).

Records immutable dual-timestamp events for causal strategy research.
Does not alter fees, fills, PnL, ranking, or execution.
"""

SCHEMA_VERSION = "research_md_v1"

__all__ = ["SCHEMA_VERSION"]
