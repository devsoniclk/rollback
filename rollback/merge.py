from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Checkpoint, RollbackSession


class MergeConflictError(Exception):
    """Raised when a branch merge encounters unresolved file conflicts and abort_on_conflict is True."""
    pass


@dataclass
class MergeResult:
    """Detailed summary of a branch merge operation."""
    status: str  # 'clean', 'fast-forward', 'conflict', 'up-to-date'
    source_branch: str
    target_branch: str
    ancestor_checkpoint: Optional[str] = None
    merged_files: List[str] = field(default_factory=list)
    conflicting_files: List[str] = field(default_factory=list)
    checkpoint_id: Optional[str] = None
    message: str = ""

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicting_files) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "ancestor_checkpoint": self.ancestor_checkpoint,
            "merged_files": self.merged_files,
            "conflicting_files": self.conflicting_files,
            "has_conflicts": self.has_conflicts,
            "checkpoint_id": self.checkpoint_id,
            "message": self.message,
        }


def _read_lines(path: Optional[str | Path]) -> List[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


def merge_chunks_3way(
    anc: List[str],
    tgt: List[str],
    src: List[str],
    tgt_label: str = "HEAD",
    src_label: str = "source",
) -> Tuple[List[str], bool]:
    """
    Performs 3-way line chunk merge between ancestor, target, and source versions.
    If modifications do not overlap, they are merged seamlessly.
    If modifications overlap, standard conflict markers are inserted.
    Returns (merged_lines, has_conflict).
    """
    if tgt == anc:
        return src, False
    if src == anc:
        return tgt, False
    if tgt == src:
        return tgt, False

    sm_tgt = difflib.SequenceMatcher(None, anc, tgt)
    sm_src = difflib.SequenceMatcher(None, anc, src)

    tgt_ops = [op for op in sm_tgt.get_opcodes() if op[0] != "equal"]
    src_ops = [op for op in sm_src.get_opcodes() if op[0] != "equal"]

    def overlaps(op1: Tuple[str, int, int, int, int], op2: Tuple[str, int, int, int, int]) -> bool:
        if op1[1] == op1[2] and op2[1] == op2[2] and op1[1] == op2[1]:
            return True
        return max(op1[1], op2[1]) < min(max(op1[1], op1[2]), max(op2[1], op2[2]))

    has_overlap = False
    for top in tgt_ops:
        for sop in src_ops:
            if overlaps(top, sop):
                has_overlap = True
                break
        if has_overlap:
            break

    if has_overlap:
        conflict_lines: List[str] = [
            f"<<<<<<< {tgt_label}\n",
            *tgt,
            "=======\n" if (not tgt or tgt[-1].endswith("\n")) else "\n=======\n",
            *src,
            f">>>>>>> {src_label}\n" if (not src or src[-1].endswith("\n")) else f"\n>>>>>>> {src_label}\n",
        ]
        return conflict_lines, True

    # Non-overlapping: combine changes ordered by ancestor index
    all_ops: List[Tuple[int, int, List[str]]] = []
    for _tag, i1, i2, j1, j2 in tgt_ops:
        all_ops.append((i1, i2, tgt[j1:j2]))
    for _tag, i1, i2, j1, j2 in src_ops:
        all_ops.append((i1, i2, src[j1:j2]))
    all_ops.sort(key=lambda x: (x[0], x[1]))

    merged: List[str] = []
    last_anc_idx = 0
    for i1, i2, repl_lines in all_ops:
        if i1 > last_anc_idx:
            merged.extend(anc[last_anc_idx:i1])
        merged.extend(repl_lines)
        last_anc_idx = max(last_anc_idx, i2)
    if last_anc_idx < len(anc):
        merged.extend(anc[last_anc_idx:])

    return merged, False


class MergeEngine:
    """
    3-way branch merge engine for rollback timelines.
    Identifies DAG common ancestors, executes fast-forward merges, non-overlapping
    3-way chunk merges, and formats deterministic conflict markers.
    """

    def __init__(self, session: RollbackSession):
        self.session = session

    def merge(
        self,
        source_branch: str,
        target_branch: Optional[str] = None,
        abort_on_conflict: bool = False,
    ) -> MergeResult:
        tgt_branch = target_branch if target_branch else self.session.current_branch

        if source_branch not in self.session.branches:
            raise ValueError(f"Source branch '{source_branch}' does not exist.")
        if tgt_branch not in self.session.branches:
            raise ValueError(f"Target branch '{tgt_branch}' does not exist.")

        source_cp_id = self.session.branches[source_branch]
        target_cp_id = self.session.branches[tgt_branch]

        if source_cp_id == target_cp_id:
            return MergeResult(
                status="up-to-date",
                source_branch=source_branch,
                target_branch=tgt_branch,
                ancestor_checkpoint=source_cp_id,
                message="Already up to date.",
            )

        # Switch to target branch if not active
        if self.session.current_branch != tgt_branch:
            self.session.checkout(tgt_branch)

        ancestor_cp_id = self.session.find_common_ancestor(source_cp_id, target_cp_id)
        if not ancestor_cp_id:
            ancestor_cp_id = self.session.history[0] if self.session.history else target_cp_id

        # Fast-forward check: if target is the common ancestor, advance target to source
        if target_cp_id == ancestor_cp_id:
            self.session.revert(source_cp_id)
            self.session.branches[tgt_branch] = source_cp_id
            self.session._save_branches()
            return MergeResult(
                status="fast-forward",
                source_branch=source_branch,
                target_branch=tgt_branch,
                ancestor_checkpoint=ancestor_cp_id,
                checkpoint_id=source_cp_id,
                message=f"Fast-forwarded {tgt_branch} to {source_branch} ({source_cp_id}).",
            )

        # If source is the common ancestor, target is already ahead
        if source_cp_id == ancestor_cp_id:
            return MergeResult(
                status="up-to-date",
                source_branch=source_branch,
                target_branch=tgt_branch,
                ancestor_checkpoint=ancestor_cp_id,
                message=f"Branch '{tgt_branch}' already contains all changes from '{source_branch}'.",
            )

        # 3-Way Merge
        anc_cp = self.session.get_checkpoint(ancestor_cp_id)
        src_cp = self.session.get_checkpoint(source_cp_id)
        tgt_cp = self.session.get_checkpoint(target_cp_id)

        if not anc_cp or not src_cp or not tgt_cp:
            raise ValueError("Failed to retrieve checkpoints required for 3-way merge.")

        all_paths: Set[str] = set(anc_cp.files.keys()) | set(src_cp.files.keys()) | set(tgt_cp.files.keys())
        merged_files: List[str] = []
        conflicts: List[str] = []

        for p in sorted(all_paths):
            s_anc = anc_cp.files.get(p)
            s_src = src_cp.files.get(p)
            s_tgt = tgt_cp.files.get(p)

            anc_exists = s_anc.exists if s_anc else False
            src_exists = s_src.exists if s_src else False
            tgt_exists = s_tgt.exists if s_tgt else False

            anc_sha = s_anc.sha256 if (s_anc and anc_exists) else None
            src_sha = s_src.sha256 if (s_src and src_exists) else None
            tgt_sha = s_tgt.sha256 if (s_tgt and tgt_exists) else None

            # Case 1: Source and target agree
            if src_sha == tgt_sha and src_exists == tgt_exists:
                continue

            # Case 2: Target changed, source untouched -> keep target
            if src_sha == anc_sha and src_exists == anc_exists:
                continue

            # Case 3: Source changed, target untouched -> accept source
            if tgt_sha == anc_sha and tgt_exists == anc_exists:
                dest = self.session.root / p
                if src_exists:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    src_blob = s_src.backup_path if (s_src and s_src.backup_path) else None
                    if src_blob and Path(src_blob).exists():
                        dest.write_bytes(Path(src_blob).read_bytes())
                    merged_files.append(p)
                else:
                    if dest.exists():
                        dest.unlink()
                    merged_files.append(p)
                continue

            # Case 4: Both changed differently -> 3-way content merge or conflict
            if not src_exists or not tgt_exists:
                # One deleted, one modified/created -> conflict
                conflicts.append(p)
                continue

            # Both exist with different hashes -> line merge
            anc_blob = s_anc.backup_path if (s_anc and anc_exists) else None
            src_blob = s_src.backup_path if (s_src and src_exists) else None
            tgt_blob = s_tgt.backup_path if (s_tgt and tgt_exists) else None

            lines_anc = _read_lines(anc_blob)
            lines_src = _read_lines(src_blob)
            lines_tgt = _read_lines(tgt_blob)

            merged_lines, conflict = merge_chunks_3way(
                lines_anc,
                lines_tgt,
                lines_src,
                tgt_label=f"HEAD ({tgt_branch})",
                src_label=source_branch,
            )

            dest = self.session.root / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.writelines(merged_lines)

            if conflict:
                conflicts.append(p)
            else:
                merged_files.append(p)

        if conflicts and abort_on_conflict:
            # Revert back to target HEAD
            self.session.revert(target_cp_id)
            raise MergeConflictError(
                f"Automatic merge aborted due to conflicts in {len(conflicts)} file(s): {', '.join(conflicts)}"
            )

        # Create merge checkpoint
        status_label = "conflict" if conflicts else "clean"
        cp_label = f"Merge branch '{source_branch}' into '{tgt_branch}'"
        merge_cp = self.session.checkpoint(label=cp_label)

        return MergeResult(
            status=status_label,
            source_branch=source_branch,
            target_branch=tgt_branch,
            ancestor_checkpoint=ancestor_cp_id,
            merged_files=merged_files,
            conflicting_files=conflicts,
            checkpoint_id=merge_cp.id,
            message=f"Merged '{source_branch}' into '{tgt_branch}' with {len(merged_files)} file(s) updated, {len(conflicts)} conflict(s).",
        )
