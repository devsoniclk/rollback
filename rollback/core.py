from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .security import SecurityPolicy, SecurityViolationError


@dataclass
class FileState:
    path: str
    exists: bool
    sha256: Optional[str] = None
    size: int = 0
    mode: Optional[int] = None
    backup_path: Optional[str] = None


@dataclass
class Checkpoint:
    id: str
    label: str
    timestamp: float
    files: Dict[str, FileState] = field(default_factory=dict)
    parent_id: Optional[str] = None
    branch: str = "main"


@dataclass
class RevertPlan:
    """Preview of filesystem mutations that will occur upon revert."""
    target_checkpoint_id: str
    target_label: str
    files_to_restore: List[str] = field(default_factory=list)
    files_to_delete: List[str] = field(default_factory=list)
    files_to_resurrect: List[str] = field(default_factory=list)
    total_bytes_affected: int = 0

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
            "total_bytes_affected": self.total_bytes_affected,
            "total_changes": self.total_changes,
        }


class RollbackSession:
    """
    Manages a transactional workspace session for an autonomous AI agent.
    Provides sub-5ms atomic checkpoints, security boundaries, diff previews, tags,
    branching timelines, dry-run revert planning, and portable bundle archiving.
    """

    DEFAULT_IGNORES = {
        ".git",
        ".rollback",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
        "node_modules",
        ".DS_Store",
    }

    def __init__(
        self,
        root_dir: str = ".",
        journal_dir: Optional[str] = None,
        ignore_patterns: Optional[Set[str]] = None,
        security_policy: Optional[SecurityPolicy] = None,
    ):
        self.root = Path(root_dir).resolve()
        self.journal_dir = Path(journal_dir) if journal_dir else self.root / ".rollback"
        self.blobs_dir = self.journal_dir / "blobs"
        self.checkpoints_dir = self.journal_dir / "checkpoints"
        self.ignores = set(self.DEFAULT_IGNORES) if ignore_patterns is None else set(ignore_patterns)
        self.security_policy = security_policy

        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        self.history: List[str] = []
        self.tags: Dict[str, str] = {}
        self.branches: Dict[str, str] = {}
        self.current_branch: str = "main"

        self._load_history()
        self._load_tags()
        self._load_branches()

    def _should_ignore(self, rel_path: Path) -> bool:
        parts = rel_path.parts
        for part in parts:
            if part in self.ignores:
                return True
        return False

    def _hash_file(self, filepath: Path) -> Tuple[str, int, int]:
        hasher = hashlib.sha256()
        size = 0
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
                size += len(chunk)
        mode = filepath.stat().st_mode
        return hasher.hexdigest(), size, mode

    def _store_blob(self, src: Path, digest: str) -> str:
        dest = self.blobs_dir / digest[:2] / digest
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(src, dest)
        return str(dest)

    def scan_workspace(self, target_files: Optional[List[str]] = None) -> Dict[str, FileState]:
        """Scans workspace or target files and records current state."""
        states: Dict[str, FileState] = {}

        if target_files:
            for tf in target_files:
                if self.security_policy:
                    self.security_policy.validate_target_path(self.root / tf)

                p = (self.root / tf).resolve()
                try:
                    rel = p.relative_to(self.root).as_posix()
                except ValueError:
                    continue
                if self._should_ignore(Path(rel)):
                    continue
                if p.exists() and p.is_file():
                    digest, size, mode = self._hash_file(p)
                    backup = self._store_blob(p, digest)
                    states[rel] = FileState(path=rel, exists=True, sha256=digest, size=size, mode=mode, backup_path=backup)
                else:
                    states[rel] = FileState(path=rel, exists=False)
            return states

        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in self.ignores]
            for file in files:
                p = Path(root) / file
                try:
                    rel = p.relative_to(self.root).as_posix()
                except ValueError:
                    continue
                if self._should_ignore(Path(rel)):
                    continue
                try:
                    digest, size, mode = self._hash_file(p)
                    backup = self._store_blob(p, digest)
                    states[rel] = FileState(path=rel, exists=True, sha256=digest, size=size, mode=mode, backup_path=backup)
                except (PermissionError, FileNotFoundError):
                    continue

        return states

    def checkpoint(self, label: str = "snapshot", target_files: Optional[List[str]] = None) -> Checkpoint:
        """Creates an immutable checkpoint of the workspace state."""
        cid = f"cp_{int(time.time() * 1000)}_{len(self.history):04d}"
        branch_head = self.branches.get(self.current_branch)
        parent = branch_head if branch_head else (self.history[-1] if self.history else None)
        files = self.scan_workspace(target_files)

        cp = Checkpoint(
            id=cid,
            label=label,
            timestamp=time.time(),
            files=files,
            parent_id=parent,
            branch=self.current_branch,
        )

        cp_file = self.checkpoints_dir / f"{cid}.json"
        with open(cp_file, "w") as f:
            json.dump(asdict(cp), f, indent=2)

        self.history.append(cid)
        self.branches[self.current_branch] = cid
        self._save_history()
        self._save_branches()

        return cp

    def resolve_checkpoint_id(self, target: str) -> str:
        """Resolves a checkpoint identifier from an exact ID, tag, branch, or HEAD alias."""
        if target.upper() == "HEAD":
            if not self.history:
                raise ValueError("No checkpoints exist in history.")
            return self.history[-1]

        if target in self.tags:
            return self.tags[target]

        if target in self.branches:
            return self.branches[target]

        if target in self.history:
            return target

        for cid in self.history:
            if cid.startswith(target):
                return cid

        raise ValueError(f"Checkpoint or tag/branch '{target}' not found in history.")

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        cid = self.resolve_checkpoint_id(checkpoint_id)
        cp_file = self.checkpoints_dir / f"{cid}.json"
        if not cp_file.exists():
            return None
        with open(cp_file, "r") as f:
            data = json.load(f)
        files = {k: FileState(**v) for k, v in data.get("files", {}).items()}
        return Checkpoint(
            id=data["id"],
            label=data["label"],
            timestamp=data["timestamp"],
            files=files,
            parent_id=data.get("parent_id"),
            branch=data.get("branch", "main"),
        )

    def plan_revert(self, checkpoint_id: str = "HEAD") -> RevertPlan:
        """Generates a dry-run plan of operations required to revert without modifying disk."""
        cid = self.resolve_checkpoint_id(checkpoint_id)
        cp = self.get_checkpoint(cid)
        if not cp:
            raise ValueError(f"Checkpoint '{cid}' does not exist.")

        current_states = self.scan_workspace()
        files_to_restore = []
        files_to_delete = []
        files_to_resurrect = []
        total_bytes = 0

        # Check files in checkpoint
        for rel_path, state in cp.files.items():
            curr = current_states.get(rel_path)
            if not state.exists:
                if curr and curr.exists:
                    files_to_delete.append(rel_path)
            elif not curr or not curr.exists:
                files_to_resurrect.append(rel_path)
                total_bytes += state.size
            elif curr.sha256 != state.sha256:
                files_to_restore.append(rel_path)
                total_bytes += state.size

        # Check newly created files not in checkpoint
        for rel_path, curr in current_states.items():
            if rel_path not in cp.files and curr.exists:
                if rel_path not in files_to_delete:
                    files_to_delete.append(rel_path)

        return RevertPlan(
            target_checkpoint_id=cid,
            target_label=cp.label,
            files_to_restore=sorted(files_to_restore),
            files_to_delete=sorted(files_to_delete),
            files_to_resurrect=sorted(files_to_resurrect),
            total_bytes_affected=total_bytes,
        )

    def revert(self, checkpoint_id: str = "HEAD", dry_run: bool = False) -> Union[Dict[str, str], RevertPlan]:
        """
        Reverts the workspace to match the target checkpoint.
        If dry_run=True, returns RevertPlan without mutating disk.
        """
        if dry_run:
            return self.plan_revert(checkpoint_id)

        cid = self.resolve_checkpoint_id(checkpoint_id)
        cp = self.get_checkpoint(cid)
        if not cp:
            raise ValueError(f"Checkpoint '{cid}' does not exist.")

        current_states = self.scan_workspace()
        actions: Dict[str, str] = {}

        # 1. Clean up newly created files that did not exist in checkpoint
        for rel_path, curr_state in current_states.items():
            if rel_path not in cp.files:
                target_file = self.root / rel_path
                if self.security_policy:
                    self.security_policy.validate_target_path(target_file)
                if target_file.exists() and target_file.is_file():
                    target_file.unlink()
                    actions[rel_path] = "removed"

        # 2. Revert / restore modified, deleted, or missing files
        for rel_path, state in cp.files.items():
            target_file = self.root / rel_path
            if self.security_policy:
                self.security_policy.validate_target_path(target_file)

            if not state.exists:
                if target_file.exists() and target_file.is_file():
                    target_file.unlink()
                    actions[rel_path] = "removed"
                continue

            curr = current_states.get(rel_path)
            if curr and curr.exists and curr.sha256 == state.sha256:
                continue

            if not state.backup_path or not Path(state.backup_path).exists():
                raise RuntimeError(f"Corrupted journal: backup blob missing for {rel_path}")

            target_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_swap = target_file.with_suffix(".tmp_rb_swap")
            shutil.copy2(state.backup_path, tmp_swap)
            if state.mode:
                tmp_swap.chmod(state.mode)
            tmp_swap.replace(target_file)
            actions[rel_path] = "restored"

        return actions

    def rollback_step(self, steps: int = 1) -> Dict[str, str]:
        """Rolls back the workspace state by N steps in history."""
        if not self.history or len(self.history) <= steps:
            raise IndexError("Cannot rollback beyond initial checkpoint.")
        target_idx = len(self.history) - 1 - steps
        target_cid = self.history[target_idx]
        actions = self.revert(target_cid)
        self.history = self.history[: target_idx + 1]
        self._save_history()
        return actions

    def tag(self, checkpoint_id: str, tag_name: str) -> None:
        """Assigns a human-readable tag alias to a checkpoint."""
        cid = self.resolve_checkpoint_id(checkpoint_id)
        self.tags[tag_name] = cid
        self._save_tags()

    def get_tag(self, tag_name: str) -> Optional[str]:
        return self.tags.get(tag_name)

    def list_tags(self) -> Dict[str, str]:
        return dict(self.tags)

    def create_branch(self, branch_name: str, from_checkpoint: Optional[str] = None) -> str:
        """Creates a new timeline branch pointing to the specified or current HEAD checkpoint."""
        cid = self.resolve_checkpoint_id(from_checkpoint if from_checkpoint else "HEAD")
        self.branches[branch_name] = cid
        self._save_branches()
        return cid

    def checkout(self, branch_or_tag: str) -> Dict[str, str]:
        """Switches workspace to the target branch or tag and restores files."""
        cid = self.resolve_checkpoint_id(branch_or_tag)
        actions = self.revert(cid)
        if branch_or_tag in self.branches:
            self.current_branch = branch_or_tag
            self._save_branches()
        return actions

    def list_branches(self) -> Dict[str, Any]:
        """Returns branch dictionary with current active branch marked."""
        return {
            "current": self.current_branch,
            "branches": dict(self.branches),
        }

    def find_common_ancestor(self, cp1_id: str, cp2_id: str) -> Optional[str]:
        """Traces DAG parent links to find the lowest common ancestor of two checkpoints."""
        cid1 = self.resolve_checkpoint_id(cp1_id)
        cid2 = self.resolve_checkpoint_id(cp2_id)

        ancestors1: Set[str] = set()
        curr: Optional[str] = cid1
        while curr:
            ancestors1.add(curr)
            cp = self.get_checkpoint(curr)
            curr = cp.parent_id if cp else None

        curr = cid2
        while curr:
            if curr in ancestors1:
                return curr
            cp = self.get_checkpoint(curr)
            curr = cp.parent_id if cp else None

        return self.history[0] if self.history else None

    def merge_branch(
        self,
        source_branch: str,
        target_branch: Optional[str] = None,
        abort_on_conflict: bool = False,
    ) -> Any:
        """Merges changes from source_branch into target_branch (defaults to current branch)."""
        from .merge import MergeEngine
        return MergeEngine(self).merge(source_branch, target_branch, abort_on_conflict=abort_on_conflict)

    def export_bundle(self, output_archive: str | Path, include_blobs: bool = True) -> Path:
        """
        Packages the rollback session, checkpoints, metadata, and blobs into a portable
        compressed archive (.rbk.tar.gz) for cross-environment agent migration.
        """
        out_p = Path(output_archive).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        manifest = {
            "version": "0.3.0",
            "exported_at": time.time(),
            "root_name": self.root.name,
            "checkpoints_count": len(self.history),
            "history": self.history,
            "tags": self.tags,
            "branches": self.branches,
            "current_branch": self.current_branch,
        }
        manifest_file = self.journal_dir / "bundle_manifest.json"
        with open(manifest_file, "w") as f:
            json.dump(manifest, f, indent=2)

        with tarfile.open(out_p, "w:gz") as tar:
            tar.add(self.journal_dir, arcname=".rollback")

        if manifest_file.exists():
            manifest_file.unlink()

        return out_p

    @classmethod
    def import_bundle(
        cls,
        archive_path: str | Path,
        target_root: str | Path = ".",
        security_policy: Optional[SecurityPolicy] = None,
    ) -> "RollbackSession":
        """
        Restores a rollback session from an exported .rbk.tar.gz archive into target_root.
        """
        arch_p = Path(archive_path).resolve()
        if not arch_p.exists():
            raise FileNotFoundError(f"Bundle archive '{archive_path}' not found.")

        dest_root = Path(target_root).resolve()
        dest_root.mkdir(parents=True, exist_ok=True)
        dest_journal = dest_root / ".rollback"

        with tarfile.open(arch_p, "r:gz") as tar:
            for member in tar.getmembers():
                target_path = (dest_root / member.name).resolve()
                try:
                    target_path.relative_to(dest_root)
                except ValueError:
                    raise SecurityViolationError(f"Malicious tar entry detected outside root: {member.name}")
                if hasattr(tarfile, "data_filter"):
                    tar.extract(member, path=dest_root, filter="data")
                else:
                    tar.extract(member, path=dest_root)

        return cls(root_dir=str(dest_root), journal_dir=str(dest_journal), security_policy=security_policy)

    def clean(self, keep_last: int = 10, purge_all: bool = False) -> Dict[str, int]:
        """Prunes old checkpoints and deletes orphaned blobs."""
        if purge_all:
            retained_ids = set()
            removed_ids = set(self.history)
        else:
            keep_count = max(0, keep_last)
            retained_ids = set(self.history[-keep_count:] if keep_count > 0 else [])
            removed_ids = set(self.history[:-keep_count] if keep_count > 0 else self.history)

        checkpoints_removed = 0
        for cid in removed_ids:
            cp_file = self.checkpoints_dir / f"{cid}.json"
            if cp_file.exists():
                cp_file.unlink()
                checkpoints_removed += 1

        self.history = [cid for cid in self.history if cid in retained_ids]
        self._save_history()

        self.tags = {k: v for k, v in self.tags.items() if v in retained_ids}
        self._save_tags()

        self.branches = {k: v for k, v in self.branches.items() if v in retained_ids}
        self._save_branches()

        active_digests: Set[str] = set()
        for cid in retained_ids:
            cp = self.get_checkpoint(cid)
            if cp:
                for f_state in cp.files.values():
                    if f_state.sha256:
                        active_digests.add(f_state.sha256)

        blobs_removed = 0
        bytes_reclaimed = 0

        for root, _, files in os.walk(self.blobs_dir):
            for file in files:
                blob_path = Path(root) / file
                digest = file
                if digest not in active_digests:
                    try:
                        sz = blob_path.stat().st_size
                        blob_path.unlink()
                        blobs_removed += 1
                        bytes_reclaimed += sz
                    except (OSError, FileNotFoundError):
                        pass

        for root, dirs, _ in os.walk(self.blobs_dir, topdown=False):
            for d in dirs:
                dir_path = Path(root) / d
                try:
                    dir_path.rmdir()
                except OSError:
                    pass

        return {
            "checkpoints_removed": checkpoints_removed,
            "blobs_removed": blobs_removed,
            "bytes_reclaimed": bytes_reclaimed,
        }

    def _save_history(self) -> None:
        hist_path = self.journal_dir / "history.json"
        with open(hist_path, "w") as f:
            json.dump(self.history, f)

    def to_html(
        self,
        output_path: Optional[Union[str, Path]] = None,
        title: str = "Rollback Session Dossier",
    ) -> str:
        """Renders an interactive dark-mode HTML visual dossier for this session."""
        from .html_report import render_html_report
        return render_html_report(self, output_path=output_path, title=title)

    def replay(
        self,
        target_identifier: str,
        step_by_step: bool = False,
        dry_run: bool = False,
        on_step: Optional[Any] = None,
    ) -> Any:
        """Deterministically replays workspace state to target checkpoint or tag."""
        from .replay import ReplayEngine
        engine = ReplayEngine(self)
        return engine.replay(
            target_identifier=target_identifier,
            step_by_step=step_by_step,
            dry_run=dry_run,
            on_step=on_step,
        )

    def _load_history(self) -> None:
        hist_path = self.journal_dir / "history.json"
        if hist_path.exists():
            with open(hist_path, "r") as f:
                self.history = json.load(f)

    def _save_tags(self) -> None:
        tags_path = self.journal_dir / "tags.json"
        with open(tags_path, "w") as f:
            json.dump(self.tags, f)

    def _load_tags(self) -> None:
        tags_path = self.journal_dir / "tags.json"
        if tags_path.exists():
            with open(tags_path, "r") as f:
                self.tags = json.load(f)

    def _save_branches(self) -> None:
        branch_path = self.journal_dir / "branches.json"
        payload = {
            "current": self.current_branch,
            "branches": self.branches,
        }
        with open(branch_path, "w") as f:
            json.dump(payload, f)

    def _load_branches(self) -> None:
        branch_path = self.journal_dir / "branches.json"
        if branch_path.exists():
            try:
                with open(branch_path, "r") as f:
                    data = json.load(f)
                    self.current_branch = data.get("current", "main")
                    self.branches = data.get("branches", {})
            except Exception:
                pass


def create_session(root_dir: str = ".", security_policy: Optional[SecurityPolicy] = None) -> RollbackSession:
    return RollbackSession(root_dir, security_policy=security_policy)
