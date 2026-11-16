"""
rollback: Transactional filesystem & state rollback engine for autonomous AI coding agents.

Provides microsecond transactional savepoints, workspace security boundaries,
dry-run revert plans, branching timelines, 3-way branch merging with conflict markers,
Git bidirectional synchronization bridge, unified diff patch export/apply,
portable bundle archiving, deterministic time-travel replay, interactive HTML dossiers,
standard MCP tool server, unified diff inspection, tagging, garbage collection,
selective reverts & cherry-picks, structural integrity gate, workspace sentinel,
auto-healing execution watchdog, speculative memory overlay sandbox, multi-agent workspace leases,
and atomic rollbacks for AI agent tool execution loops.
"""

from .core import Checkpoint, RevertPlan, RollbackSession, create_session
from .diff import DiffSummary, FileDelta, compute_diff
from .git_bridge import GitBridge, GitSyncStatus
from .healer import (
    AutoHealer,
    DiagnosticParser,
    FailureClassification,
    HealingIncidentReport,
)
from .html_report import render_html_report
from .integrity import FileIntegrityResult, IntegrityError, IntegrityGate, IntegrityReport
from .lease import LeaseConflictError, WorkspaceLease, WorkspaceLeaseManager
from .mcp_server import RollbackMCPServer
from .merge import MergeConflictError, MergeEngine, MergeResult
from .middleware import async_transactional_step, transactional_step, transactional_tool
from .patch import PatchApplyResult, apply_patch, export_patch
from .replay import ReplayEngine, ReplayStep, ReplaySummary
from .sandbox import SandboxCommitResult, SpeculativeSandbox
from .security import SecurityPolicy, SecurityViolationError
from .selective import (
    SelectiveFileOperation,
    SelectiveRevertEngine,
    SelectiveRevertError,
    SelectiveRevertPlan,
)
from .sentinel import DriftDetectedError, DriftFile, DriftReport, WorkspaceSentinel

__version__ = "0.7.0"
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
    "render_html_report",
    "ReplayEngine",
    "ReplayStep",
    "ReplaySummary",
    "RollbackMCPServer",
    "MergeEngine",
    "MergeResult",
    "MergeConflictError",
    "GitBridge",
    "GitSyncStatus",
    "export_patch",
    "apply_patch",
    "PatchApplyResult",
    "SelectiveRevertEngine",
    "SelectiveRevertPlan",
    "SelectiveFileOperation",
    "SelectiveRevertError",
    "IntegrityGate",
    "FileIntegrityResult",
    "IntegrityReport",
    "IntegrityError",
    "WorkspaceSentinel",
    "DriftReport",
    "DriftFile",
    "DriftDetectedError",
    "AutoHealer",
    "HealingIncidentReport",
    "FailureClassification",
    "DiagnosticParser",
    "SpeculativeSandbox",
    "SandboxCommitResult",
    "WorkspaceLeaseManager",
    "WorkspaceLease",
    "LeaseConflictError",
    "transactional_tool",
    "transactional_step",
    "async_transactional_step",
]
