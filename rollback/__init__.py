"""
rollback: Transactional filesystem & state rollback engine for autonomous AI coding agents.

Provides microsecond transactional savepoints, copy-on-write journals, unified diff
inspection, tagging, garbage collection, and atomic rollbacks for AI agent tool execution loops.
"""

from .core import Checkpoint, RollbackSession, create_session
from .diff import DiffSummary, FileDelta, compute_diff
from .middleware import async_transactional_step, transactional_step, transactional_tool

__version__ = "0.2.0"
__all__ = [
    "RollbackSession",
    "create_session",
    "Checkpoint",
    "compute_diff",
    "DiffSummary",
    "FileDelta",
    "transactional_tool",
    "transactional_step",
    "async_transactional_step",
]
