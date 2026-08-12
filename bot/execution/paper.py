"""Backward-compatible export of the paper executor.

Implementation lives in ``paper_executor.py``. Live trading is never enabled here.
"""

from bot.execution.paper_executor import PaperExecutor

__all__ = ["PaperExecutor"]
