import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union
from .core import Checkpoint, RollbackSession


@dataclass
class FileDelta:
    path: str
    change_type: str  # 'added', 'modified', 'deleted', 'identical'
    lines_added: int = 0
    lines_deleted: int = 0
    patch: str = ""


@dataclass
class DiffSummary:
    from_id: str
    to_id: str
    total_files_changed: int
    files: List[FileDelta]

    @property
    def is_clean(self) -> bool:
        return self.total_files_changed == 0

    @property
    def added(self) -> List[str]:
        return [f.path for f in self.files if f.change_type == "added"]

    @property
    def modified(self) -> List[str]:
        return [f.path for f in self.files if f.change_type == "modified"]

    @property
    def deleted(self) -> List[str]:
        return [f.path for f in self.files if f.change_type == "deleted"]

    def format_diff(self) -> str:
        lines = [f"=== Diff: {self.from_id} -> {self.to_id} ({self.total_files_changed} files changed) ==="]
        for f in self.files:
            lines.append(f"[{f.change_type.upper()}] {f.path} (+{f.lines_added}, -{f.lines_deleted})")
            if f.patch:
                lines.append(f.patch)
        return "\n".join(lines)


def _read_content(path: Optional[str]) -> List[str]:
    if not path or not Path(path).exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


def compute_diff(
    session: RollbackSession,
    from_checkpoint: Union[Checkpoint, str],
    to_checkpoint: Optional[Union[Checkpoint, str]] = None,
) -> DiffSummary:
    """
    Computes unified diffs between two checkpoints, or from a checkpoint to live workspace.
    Accepts Checkpoint instances or string checkpoint IDs / tag aliases.
    """
    if isinstance(from_checkpoint, str):
        resolved_from = session.get_checkpoint(from_checkpoint)
        if not resolved_from:
            raise ValueError(f"Baseline checkpoint or tag '{from_checkpoint}' not found.")
        from_cp = resolved_from
    else:
        from_cp = from_checkpoint

    if to_checkpoint is not None:
        if isinstance(to_checkpoint, str):
            resolved_to = session.get_checkpoint(to_checkpoint)
            if not resolved_to:
                raise ValueError(f"Target checkpoint or tag '{to_checkpoint}' not found.")
            to_cp = resolved_to
        else:
            to_cp = to_checkpoint
        to_files = to_cp.files
        to_id = to_cp.id
    else:
        to_files = session.scan_workspace()
        to_id = "LIVE_WORKSPACE"

    all_paths = set(from_cp.files.keys()).union(to_files.keys())
    deltas: List[FileDelta] = []

    for rel_path in sorted(all_paths):
        s1 = from_cp.files.get(rel_path)
        s2 = to_files.get(rel_path)

        exists1 = s1.exists if s1 else False
        exists2 = s2.exists if s2 else False

        if not exists1 and exists2:
            # File added
            src2 = s2.backup_path if (s2 and s2.backup_path) else str(session.root / rel_path)
            lines2 = _read_content(src2)
            patch = "".join(difflib.unified_diff([], lines2, fromfile="/dev/null", tofile=f"b/{rel_path}"))
            deltas.append(FileDelta(path=rel_path, change_type="added", lines_added=len(lines2), lines_deleted=0, patch=patch))

        elif exists1 and not exists2:
            # File deleted
            src1 = s1.backup_path if s1 else None
            lines1 = _read_content(src1)
            patch = "".join(difflib.unified_diff(lines1, [], fromfile=f"a/{rel_path}", tofile="/dev/null"))
            deltas.append(FileDelta(path=rel_path, change_type="deleted", lines_added=0, lines_deleted=len(lines1), patch=patch))

        elif exists1 and exists2:
            if s1 and s2 and s1.sha256 == s2.sha256:
                continue  # Identical
            # Modified
            src1 = s1.backup_path if s1 else None
            src2 = s2.backup_path if (s2 and s2.backup_path) else str(session.root / rel_path)
            lines1 = _read_content(src1)
            lines2 = _read_content(src2)

            added = 0
            deleted = 0
            for l in difflib.unified_diff(lines1, lines2):
                if l.startswith("+") and not l.startswith("+++"):
                    added += 1
                elif l.startswith("-") and not l.startswith("---"):
                    deleted += 1

            patch = "".join(difflib.unified_diff(lines1, lines2, fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}"))
            deltas.append(FileDelta(path=rel_path, change_type="modified", lines_added=added, lines_deleted=deleted, patch=patch))

    return DiffSummary(from_id=from_cp.id, to_id=to_id, total_files_changed=len(deltas), files=deltas)
