"""
rollback.security: Workspace containment and safety enforcement policy for AI coding agents.

Guards against path traversal, symlink directory escapes, rogue file creation, and
unauthorized modification of sensitive files (.env, private keys, system files).
"""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set


class SecurityViolationError(Exception):
    """Raised when an AI agent attempts an action violating the workspace security policy."""
    pass


@dataclass
class SecurityPolicy:
    """
    Enforces strict security boundaries on autonomous AI agent filesystem actions.
    """
    allowed_root: Path
    prevent_traversal: bool = True
    block_symlink_escape: bool = True
    protected_patterns: List[str] = field(default_factory=lambda: [
        ".env*",
        "*.pem",
        "*.key",
        "*.pkcs12",
        "*.pfx",
        "id_rsa*",
        "id_ed25519*",
        "*credentials*",
        "*.secret*",
    ])
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB threshold

    def __post_init__(self):
        self.allowed_root = Path(self.allowed_root).resolve()

    def is_protected(self, rel_path: str) -> bool:
        """Checks whether a relative path matches any sensitive file patterns."""
        p = Path(rel_path)
        name = p.name
        as_posix = p.as_posix()
        for pat in self.protected_patterns:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(as_posix, pat):
                return True
        return False

    def validate_target_path(self, target_path: Path | str, allow_symlinks: bool = False) -> Path:
        """
        Validates that a path resides strictly within `allowed_root` and complies with policy.
        Returns the resolved Path or raises SecurityViolationError.
        """
        p = Path(target_path)
        if not p.is_absolute():
            p = (self.allowed_root / p)

        # 1. Path traversal check (prevent ../ escape)
        try:
            resolved = p.resolve()
        except Exception as e:
            raise SecurityViolationError(f"Failed to resolve path '{target_path}': {e}")

        try:
            rel = resolved.relative_to(self.allowed_root)
        except ValueError:
            raise SecurityViolationError(
                f"Path traversal detected: '{target_path}' resolves outside allowed root '{self.allowed_root}'"
            )

        rel_str = rel.as_posix()

        # 2. Symlink escape check
        if self.block_symlink_escape and not allow_symlinks:
            # Check if any parent or the file itself is a symlink pointing outside
            curr = p
            while curr != self.allowed_root and curr != curr.parent:
                if curr.is_symlink():
                    link_target = curr.resolve()
                    try:
                        link_target.relative_to(self.allowed_root)
                    except ValueError:
                        raise SecurityViolationError(
                            f"Symlink escape detected: symlink '{curr}' points outside root to '{link_target}'"
                        )
                curr = curr.parent

        # 3. Protected files check
        if self.is_protected(rel_str):
            raise SecurityViolationError(
                f"Access denied: file '{rel_str}' matches protected sensitive pattern in security policy"
            )

        # 4. File size threshold check (if file already exists)
        if resolved.exists() and resolved.is_file():
            try:
                sz = resolved.stat().st_size
                if sz > self.max_file_size_bytes:
                    raise SecurityViolationError(
                        f"File size limit exceeded: '{rel_str}' is {sz} bytes (max {self.max_file_size_bytes})"
                    )
            except OSError:
                pass

        return resolved
