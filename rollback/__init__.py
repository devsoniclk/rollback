"""
rollback: Transactional filesystem & state rollback engine for autonomous AI coding agents.

Provides microsecond transactional savepoints, workspace security boundaries,
dry-run revert plans, branching timelines, portable bundle archiving,
unified diff inspection, tagging, garbage collection, and atomic rollbacks
for AI agent tool execution loops.
"""

from .core import Checkpoint, RevertPlan, RollbackSession, create_session
from .diff import DiffSummary, FileDelta, compute_diff
from .middleware import async_transactional_step, transactional_step, transactional_tool
from .security import SecurityPolicy, SecurityViolationError

__version__ = "0.3.0"
__all__ = [
    "RollbackSession",
    "create_session",
    "Checkpoint",
    "RevertPlan",
    "SecurityPolicy",
    "SecurityViolationError",
    "compute_diff",
    "DiffSummary",
    "FileDelta",
    "transactional_tool",
    "transactional_step",
    "async_transactional_step",
]
