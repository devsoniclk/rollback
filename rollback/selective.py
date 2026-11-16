from __future__ import annotations

import difflib
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Union

if TYPE_CHECKING:
    from .core import Checkpoint, RollbackSession


class SelectiveRevertError(Exception):
    """Raised when a selective revert or cherry-pick operation cannot be completed."""
    pass


@dataclass
class SelectiveFileOperation:
    path: str
    action: str  # "restore", "delete", "resurrect", "skip"
    source_sha256: Optional[str] = None
    current_sha256: Optional[str] = None
    size: int = 0
    diff: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "source_sha256": self.source_sha256,
            "current_sha256": self.current_sha256,
            "size": self.size,
            "has_diff": bool(self.diff),
        }


@dataclass
class SelectiveRevertPlan:
    target_checkpoint_id: str
    target_label: str
    operations: List[SelectiveFileOperation] = field(default_factory=list)
    total_bytes_affected: int = 0

    @property
    def files_to_restore(self) -> List[str]:
        return [op.path for op in self.operations if op.action == "restore"]

    @property
    def files_to_delete(self) -> List[str]:
        return [op.path for op in self.operations if op.action == "delete"]

    @property
    def files_to_resurrect(self) -> List[str]:
        return [op.path for op in self.operations if op.action == "resurrect"]

    @property
    def files_skipped(self) -> List[str]:
        return [op.path for op in self.operations if op.action == "skip"]

    @property
    def total_changes(self) -> int:
        return len(self.files_to_restore) + len(self.files_to_delete) + len(self.files_to_resurrect)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_checkpoint_id": self.target_checkpoint_id,
            "target_label": self.target_label,
            "files_to_restore": self.files_to_restore,
            "files_to_delete": self.files_to_delete,
            "files_to_resurrect": self.files_to_resurrect,
            "files_skipped": self.files_skipped,
            "total_bytes_affected": self.total_bytes_affected,
            "total_changes": self.total_changes,
            "operations": [op.to_dict() for op in self.operations],
        }


class SelectiveRevertEngine:
    """
    Precision file-level rollback and cherry-pick engine.
    Allows reverting or importing individual files from past checkpoints or other branches
    without rolling back the entire workspace.
    """

    def __init__(self, session: RollbackSession):
        self.session = session

    def _resolve_blob_path(self, state: Any) -> Optional[Path]:
        if getattr(state, "backup_path", None) and Path(state.backup_path).exists():
            return Path(state.backup_path)
        sha = getattr(state, "sha256", None)
        if sha:
            p1 = self.session.blobs_dir / sha[:2] / sha
            if p1.exists():
                return p1
            p2 = self.session.blobs_dir / sha
            if p2.exists():
                return p2
        return None

    def _normalize_path(self, file_path: Union[str, Path]) -> str:
        p = Path(file_path)
        if p.is_absolute():
            try:
                rel = p.relative_to(self.session.root)
            except ValueError:
                raise SelectiveRevertError(f"Path '{file_path}' is outside workspace root '{self.session.root}'.")
        else:
            rel = p
        rel_str = str(rel).replace("\\", "/")
        if self.session.security_policy:
            self.session.security_policy.validate_path(self.session.root / rel_str, self.session.root)
        return rel_str

    def plan_selective_revert(
        self,
        checkpoint_id: str,
        file_paths: List[Union[str, Path]],
    ) -> SelectiveRevertPlan:
        """
        Inspects intended target files and constructs an execution plan showing
        which files will be restored, deleted, resurrected, or skipped.
        """
        cid = self.session.resolve_checkpoint_id(checkpoint_id)
        cp = self.session.get_checkpoint(cid)
        if not cp:
            raise SelectiveRevertError(f"Checkpoint '{cid}' does not exist.")

        current_states = self.session.scan_workspace()
        operations: List[SelectiveFileOperation] = []
        total_bytes = 0

        for raw_path in file_paths:
            rel_path = self._normalize_path(raw_path)
            curr = current_states.get(rel_path)
            target_state = cp.files.get(rel_path)

            if target_state is None or not target_state.exists:
                # File did not exist at the target checkpoint
                if curr and curr.exists:
                    # Current file exists, so revert means deleting it to match target state
                    operations.append(
                        SelectiveFileOperation(
                            path=rel_path,
                            action="delete",
                            current_sha256=curr.sha256,
                            size=curr.size,
                        )
                    )
                    total_bytes += curr.size
                else:
                    # Neither existed then nor exists now: skip
                    operations.append(
                        SelectiveFileOperation(
                            path=rel_path,
                            action="skip",
                            size=0,
                        )
                    )
            else:
                # Target state exists in checkpoint
                blob_path = self._resolve_blob_path(target_state)
                if not blob_path or not blob_path.exists():
                    raise SelectiveRevertError(
                        f"Blob for '{rel_path}' (hash {target_state.sha256}) missing from journal."
                    )

                if curr is None or not curr.exists:
                    # File was deleted locally, needs resurrection
                    diff_text = None
                    try:
                        with open(blob_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        diff_text = f"--- /dev/null\n+++ b/{rel_path}\n@@ -0,0 +1,{len(content.splitlines())} @@\n"
                    except Exception:
                        pass

                    operations.append(
                        SelectiveFileOperation(
                            path=rel_path,
                            action="resurrect",
                            source_sha256=target_state.sha256,
                            size=target_state.size,
                            diff=diff_text,
                        )
                    )
                    total_bytes += target_state.size
                elif curr.sha256 != target_state.sha256:
                    # File modified locally, needs restoration
                    diff_text = None
                    try:
                        disk_path = self.session.root / rel_path
                        with open(disk_path, "r", encoding="utf-8", errors="replace") as f:
                            curr_lines = f.readlines()
                        with open(blob_path, "r", encoding="utf-8", errors="replace") as f:
                            target_lines = f.readlines()
                        diff_gen = difflib.unified_diff(
                            curr_lines,
                            target_lines,
                            fromfile=f"a/{rel_path} (current)",
                            tofile=f"b/{rel_path} ({cid[:8]})",
                        )
                        diff_text = "".join(diff_gen)
                    except Exception:
                        pass

                    operations.append(
                        SelectiveFileOperation(
                            path=rel_path,
                            action="restore",
                            source_sha256=target_state.sha256,
                            current_sha256=curr.sha256,
                            size=target_state.size,
                            diff=diff_text,
                        )
                    )
                    total_bytes += target_state.size
                else:
                    # File is identical: skip
                    operations.append(
                        SelectiveFileOperation(
                            path=rel_path,
                            action="skip",
                            source_sha256=target_state.sha256,
                            current_sha256=curr.sha256,
                            size=target_state.size,
                        )
                    )

        return SelectiveRevertPlan(
            target_checkpoint_id=cid,
            target_label=cp.label,
            operations=operations,
            total_bytes_affected=total_bytes,
        )

    def revert_files(
        self,
        checkpoint_id: str,
        file_paths: List[Union[str, Path]],
        dry_run: bool = False,
        backup_checkpoint: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes selective rollback for the specified files.
        Optionally takes an automatic backup checkpoint before applying changes.
        """
        plan = self.plan_selective_revert(checkpoint_id, file_paths)
        if dry_run:
            return {"dry_run": True, "plan": plan.to_dict()}

        backup_id = None
        if backup_checkpoint and plan.total_changes > 0:
            cp_backup = self.session.checkpoint(label=f"pre_selective_revert_{plan.target_checkpoint_id[:8]}")
            backup_id = cp_backup.id

        cid = plan.target_checkpoint_id
        cp = self.session.get_checkpoint(cid)
        if not cp:
            raise SelectiveRevertError(f"Checkpoint '{cid}' not found.")

        results: Dict[str, str] = {}
        for op in plan.operations:
            target_disk_path = self.session.root / op.path

            if op.action == "delete":
                if target_disk_path.exists():
                    if target_disk_path.is_file():
                        target_disk_path.unlink()
                    elif target_disk_path.is_dir():
                        shutil.rmtree(target_disk_path)
                    results[op.path] = "deleted"

            elif op.action in ("restore", "resurrect"):
                state = cp.files.get(op.path)
                if not state or not state.sha256:
                    continue
                blob_path = self._resolve_blob_path(state)
                if not blob_path or not blob_path.exists():
                    raise SelectiveRevertError(f"Missing blob for {op.path}")
                target_disk_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(blob_path, target_disk_path)
                if state.mode is not None:
                    try:
                        os.chmod(target_disk_path, state.mode)
                    except OSError:
                        pass
                results[op.path] = op.action

            elif op.action == "skip":
                results[op.path] = "skipped"

        return {
            "status": "success",
            "target_checkpoint_id": cid,
            "target_label": cp.label,
            "backup_checkpoint_id": backup_id,
            "total_changes": plan.total_changes,
            "files_affected": results,
            "plan": plan.to_dict(),
        }

    def cherry_pick_files(
        self,
        source_checkpoint_id: str,
        file_paths: List[Union[str, Path]],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Cherry-picks file versions from an alternate checkpoint/branch into current workspace.
        """
        return self.revert_files(
            checkpoint_id=source_checkpoint_id,
            file_paths=file_paths,
            dry_run=dry_run,
            backup_checkpoint=True,
        )
