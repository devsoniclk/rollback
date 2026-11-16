from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Union

if TYPE_CHECKING:
    from .core import Checkpoint, RollbackSession


class DriftDetectedError(Exception):
    """Raised when unexpected workspace drift is detected in clean-assertion mode."""
    pass


@dataclass
class DriftFile:
    path: str
    status: str  # "modified", "untracked", "deleted"
    current_size: int = 0
    checkpoint_size: int = 0
    size_delta: int = 0
    current_sha256: Optional[str] = None
    checkpoint_sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "current_size": self.current_size,
            "checkpoint_size": self.checkpoint_size,
            "size_delta": self.size_delta,
            "current_sha256": self.current_sha256,
            "checkpoint_sha256": self.checkpoint_sha256,
        }


@dataclass
class DriftReport:
    head_checkpoint_id: Optional[str]
    head_label: Optional[str]
    is_dirty: bool
    modified_files: List[str] = field(default_factory=list)
    untracked_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    total_delta_bytes: int = 0
    drift_ratio: float = 0.0
    files: List[DriftFile] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.modified_files) + len(self.untracked_files) + len(self.deleted_files)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "head_checkpoint_id": self.head_checkpoint_id,
            "head_label": self.head_label,
            "is_dirty": self.is_dirty,
            "total_changes": self.total_changes,
            "modified_files": self.modified_files,
            "untracked_files": self.untracked_files,
            "deleted_files": self.deleted_files,
            "total_delta_bytes": self.total_delta_bytes,
            "drift_ratio": round(self.drift_ratio, 4),
            "files": [f.to_dict() for f in self.files],
        }


class WorkspaceSentinel:
    """
    Continuous workspace observation, drift analysis, and autonomous checkpointing watchdog.
    Enables agents to detect uncommitted disk side-effects, measure drift magnitude,
    and trigger proactive micro-checkpoints before risk boundaries.
    """

    def __init__(self, session: RollbackSession):
        self.session = session

    def scan_drift(self, paths: Optional[List[str]] = None) -> DriftReport:
        """
        Inspects the live workspace against the HEAD checkpoint without modifying state.
        """
        head_id = self.session.history[-1] if self.session.history else None
        head_cp = self.session.get_checkpoint(head_id) if head_id else None

        current_states = self.session.scan_workspace()
        filter_paths = set(paths) if paths else None

        modified_files: List[str] = []
        untracked_files: List[str] = []
        deleted_files: List[str] = []
        drift_files: List[DriftFile] = []
        total_delta_bytes = 0

        if not head_cp:
            # No prior checkpoints: all current files are untracked
            for rel_path, state in sorted(current_states.items()):
                if filter_paths and rel_path not in filter_paths:
                    continue
                if state.exists:
                    untracked_files.append(rel_path)
                    drift_files.append(
                        DriftFile(
                            path=rel_path,
                            status="untracked",
                            current_size=state.size,
                            checkpoint_size=0,
                            size_delta=state.size,
                            current_sha256=state.sha256,
                        )
                    )
                    total_delta_bytes += state.size

            return DriftReport(
                head_checkpoint_id=None,
                head_label=None,
                is_dirty=len(untracked_files) > 0,
                modified_files=[],
                untracked_files=untracked_files,
                deleted_files=[],
                total_delta_bytes=total_delta_bytes,
                drift_ratio=1.0 if untracked_files else 0.0,
                files=drift_files,
            )

        # 1. Compare checkpoint tracked files against current workspace
        for rel_path, cp_file in head_cp.files.items():
            if filter_paths and rel_path not in filter_paths:
                continue

            curr = current_states.get(rel_path)
            if not cp_file.exists:
                continue

            if curr is None or not curr.exists:
                deleted_files.append(rel_path)
                drift_files.append(
                    DriftFile(
                        path=rel_path,
                        status="deleted",
                        current_size=0,
                        checkpoint_size=cp_file.size,
                        size_delta=-cp_file.size,
                        checkpoint_sha256=cp_file.sha256,
                    )
                )
                total_delta_bytes += cp_file.size
            elif curr.sha256 != cp_file.sha256:
                modified_files.append(rel_path)
                delta = curr.size - cp_file.size
                drift_files.append(
                    DriftFile(
                        path=rel_path,
                        status="modified",
                        current_size=curr.size,
                        checkpoint_size=cp_file.size,
                        size_delta=delta,
                        current_sha256=curr.sha256,
                        checkpoint_sha256=cp_file.sha256,
                    )
                )
                total_delta_bytes += abs(delta)

        # 2. Check for untracked files
        for rel_path, curr in current_states.items():
            if filter_paths and rel_path not in filter_paths:
                continue

            if curr.exists and rel_path not in head_cp.files:
                untracked_files.append(rel_path)
                drift_files.append(
                    DriftFile(
                        path=rel_path,
                        status="untracked",
                        current_size=curr.size,
                        checkpoint_size=0,
                        size_delta=curr.size,
                        current_sha256=curr.sha256,
                    )
                )
                total_delta_bytes += curr.size

        tracked_count = max(1, len([f for f in head_cp.files.values() if f.exists]))
        total_changes = len(modified_files) + len(untracked_files) + len(deleted_files)
        drift_ratio = min(1.0, total_changes / tracked_count)

        return DriftReport(
            head_checkpoint_id=head_cp.id,
            head_label=head_cp.label,
            is_dirty=total_changes > 0,
            modified_files=sorted(modified_files),
            untracked_files=sorted(untracked_files),
            deleted_files=sorted(deleted_files),
            total_delta_bytes=total_delta_bytes,
            drift_ratio=drift_ratio,
            files=drift_files,
        )

    def auto_checkpoint(
        self,
        trigger_label: str = "sentinel_auto",
        min_changes: int = 1,
        tag: Optional[str] = None,
    ) -> Optional[Checkpoint]:
        """
        Creates an automated checkpoint if observed workspace drift meets or exceeds min_changes.
        Returns the new Checkpoint or None if changes are below threshold.
        """
        drift = self.scan_drift()
        if drift.total_changes >= min_changes:
            cp = self.session.checkpoint(label=f"{trigger_label} ({drift.total_changes} changes)")
            if tag:
                self.session.tag(cp.id, tag)
            return cp
        return None

    def assert_clean(self, paths: Optional[List[str]] = None) -> None:
        """
        Asserts that the workspace has zero uncommitted drift from HEAD.
        Raises DriftDetectedError if modifications or untracked files exist.
        """
        report = self.scan_drift(paths=paths)
        if report.is_dirty:
            details = []
            if report.modified_files:
                details.append(f"modified: {', '.join(report.modified_files)}")
            if report.untracked_files:
                details.append(f"untracked: {', '.join(report.untracked_files)}")
            if report.deleted_files:
                details.append(f"deleted: {', '.join(report.deleted_files)}")
            raise DriftDetectedError(
                f"Workspace has {report.total_changes} uncommitted drift item(s): {'; '.join(details)}"
            )
