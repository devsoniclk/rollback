"""
In-memory Copy-on-Write Speculative Overlay Sandbox for AI coding agents.

Enables agents to stage code modifications, multi-file refactors, and file deletions
in a speculative memory buffer without mutating the real workspace disk until explicitly
verified against the IntegrityGate and committed atomically.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from .integrity import FileIntegrityResult, IntegrityGate, IntegrityReport


class SandboxCommitResult:
    """Result of committing a speculative sandbox session to disk."""

    def __init__(
        self,
        written_files: List[str],
        deleted_files: List[str],
        checkpoint_id: Optional[str] = None,
    ):
        self.written_files = written_files
        self.deleted_files = deleted_files
        self.checkpoint_id = checkpoint_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "written_files": self.written_files,
            "deleted_files": self.deleted_files,
            "total_mutations": len(self.written_files) + len(self.deleted_files),
            "checkpoint_id": self.checkpoint_id,
        }

    def __repr__(self) -> str:
        return (
            f"<SandboxCommitResult written={len(self.written_files)} "
            f"deleted={len(self.deleted_files)} cp={self.checkpoint_id}>"
        )


class SpeculativeSandbox:
    """In-memory virtual filesystem overlay mounted atop a RollbackSession workspace."""

    def __init__(self, session: Any):
        self.session = session
        self.workspace_dir: Path = getattr(session, "root", getattr(session, "workspace_dir", Path(".")))
        self._overlay: Dict[str, Optional[bytes]] = {}
        self._is_active = True

    def _normalize_path(self, rel_path: Union[str, Path]) -> str:
        p = Path(rel_path)
        if p.is_absolute():
            try:
                p = p.relative_to(self.workspace_dir)
            except ValueError:
                pass
        return str(p).replace("\\", "/").strip("/")

    def write_file(self, rel_path: Union[str, Path], content: Union[str, bytes]) -> None:
        """Stage a file write into the memory overlay without touching physical disk."""
        norm_path = self._normalize_path(rel_path)
        if isinstance(content, str):
            self._overlay[norm_path] = content.encode("utf-8")
        else:
            self._overlay[norm_path] = content

    def delete_file(self, rel_path: Union[str, Path]) -> None:
        """Stage a file deletion into the memory overlay."""
        norm_path = self._normalize_path(rel_path)
        self._overlay[norm_path] = None

    def read_file(
        self, rel_path: Union[str, Path], as_bytes: bool = False
    ) -> Union[str, bytes]:
        """
        Read file contents. Returns staged memory overlay content if present;
        otherwise falls through to physical disk.
        """
        norm_path = self._normalize_path(rel_path)
        if norm_path in self._overlay:
            staged = self._overlay[norm_path]
            if staged is None:
                raise FileNotFoundError(f"File staged for deletion in sandbox: {norm_path}")
            return staged if as_bytes else staged.decode("utf-8", errors="replace")

        # Fall through to disk
        real_path = self.workspace_dir / norm_path
        if not real_path.exists() or not real_path.is_file():
            raise FileNotFoundError(f"File not found on disk or in sandbox: {norm_path}")

        raw = real_path.read_bytes()
        return raw if as_bytes else raw.decode("utf-8", errors="replace")

    def exists(self, rel_path: Union[str, Path]) -> bool:
        """Check if file exists in either memory overlay or physical workspace."""
        norm_path = self._normalize_path(rel_path)
        if norm_path in self._overlay:
            return self._overlay[norm_path] is not None
        real_path = self.workspace_dir / norm_path
        return real_path.exists()

    def list_staged(self) -> Dict[str, str]:
        """List all currently staged modifications: path -> 'write' or 'delete'."""
        staged = {}
        for path, val in self._overlay.items():
            staged[path] = "delete" if val is None else "write"
        return staged

    def validate_integrity(self) -> IntegrityReport:
        """
        Validate all staged written files against structural/syntax integrity rules
        (Python AST, JSON, YAML, TOML, JavaScript, TypeScript, Markdown) without committing to disk.
        """
        results: List[FileIntegrityResult] = []

        for rel_path, content in self._overlay.items():
            if content is None:
                continue  # Deletions don't need syntax validation
            text = content.decode("utf-8", errors="replace")
            res = IntegrityGate.check_content(text, rel_path)
            results.append(res)

        failed = [r for r in results if not r.valid]
        return IntegrityReport(
            files_checked=len(results),
            passed_count=len(results) - len(failed),
            failed_count=len(failed),
            is_clean=len(failed) == 0,
            results=results,
        )

    def commit(
        self,
        create_checkpoint: bool = True,
        message: Optional[str] = None,
        validate_first: bool = True,
    ) -> SandboxCommitResult:
        """
        Atomically commit all staged writes and deletions to physical disk.
        Optionally creates a micro-checkpoint of the committed state.
        """
        if validate_first:
            report = self.validate_integrity()
            if not report.is_clean:
                errors = "; ".join(
                    f"{r.path}: {r.error_message}"
                    for r in report.failures
                )
                raise ValueError(
                    f"Integrity validation failed before commit: {errors}"
                )

        written: List[str] = []
        deleted: List[str] = []

        for rel_path, content in self._overlay.items():
            target_path = self.workspace_dir / rel_path
            if content is None:
                if target_path.exists():
                    target_path.unlink()
                    deleted.append(rel_path)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(content)
                written.append(rel_path)

        self._overlay.clear()

        cp_id = None
        if create_checkpoint and (written or deleted):
            msg = message or f"Committed speculative sandbox ({len(written)} writes, {len(deleted)} deletes)"
            cp = self.session.checkpoint(msg)
            cp_id = cp.id

        return SandboxCommitResult(
            written_files=written,
            deleted_files=deleted,
            checkpoint_id=cp_id,
        )

    def discard(self) -> None:
        """Discard all staged changes in memory."""
        self._overlay.clear()

    def __enter__(self) -> "SpeculativeSandbox":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.discard()
