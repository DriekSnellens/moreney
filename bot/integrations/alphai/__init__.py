"""AlphaI financial news integration (https://alphai.io)."""

# Keep package init light to avoid circular imports with live.micro_session.
# Prefer submodule imports: bot.integrations.alphai.regime, .features, .signals

__all__ = [
    "AlphaINewsMonitor",
    "AlphaIRegimeState",
]


def __getattr__(name: str):
    if name == "AlphaINewsMonitor":
        from bot.integrations.alphai.regime import AlphaINewsMonitor

        return AlphaINewsMonitor
    if name == "AlphaIRegimeState":
        from bot.integrations.alphai.regime import AlphaIRegimeState

        return AlphaIRegimeState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
