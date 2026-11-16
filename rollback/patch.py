from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .diff import compute_diff

if TYPE_CHECKING:
    from .core import RollbackSession


@dataclass
class PatchApplyResult:
    """Summary of patch application."""
    status: str  # 'applied', 'dry_run_success', 'failed'
    files_affected: List[str] = field(default_factory=list)
    failed_hunks: List[Dict[str, Any]] = field(default_factory=list)
    pre_checkpoint: Optional[str] = None
    post_checkpoint: Optional[str] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "files_affected": self.files_affected,
            "failed_hunks": self.failed_hunks,
            "pre_checkpoint": self.pre_checkpoint,
            "post_checkpoint": self.post_checkpoint,
            "message": self.message,
        }


def export_patch(
    session: RollbackSession,
    from_checkpoint: str,
    to_checkpoint: Optional[str] = None,
    output_file: Optional[str | Path] = None,
) -> str:
    """
    Generates a standard unified diff patch representation between two checkpoints
    or against the live workspace.
    """
    diff_summary = compute_diff(session, from_checkpoint, to_checkpoint)
    header = [
        f"# Rollback Unified Patch",
        f"# From: {diff_summary.from_id}",
        f"# To: {diff_summary.to_id}",
        f"# Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"# Files changed: {diff_summary.total_files_changed}",
        "",
    ]

    body: List[str] = []
    for f in diff_summary.files:
        if f.patch:
            body.append(f"diff --git a/{f.path} b/{f.path}")
            body.append(f.patch)
            if not f.patch.endswith("\n"):
                body.append("\n")

    full_patch = "\n".join(header) + "".join(body)

    if output_file:
        out_p = Path(output_file).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(full_patch, encoding="utf-8")

    return full_patch


def _apply_hunks_to_lines(lines: List[str], hunks: List[List[str]]) -> Tuple[List[str], bool, str]:
    """
    Applies unified diff hunks sequentially to an array of lines.
    Returns (new_lines, success, error_message).
    """
    res = list(lines)
    for hunk in hunks:
        hunk_header = hunk[0]
        m = re.match(r"^@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@", hunk_header)
        if not m:
            continue
        old_start = int(m.group(1))
        # Hunk line index is 1-based, adjust to 0-based
        idx = max(0, old_start - 1)

        # Separate lines by operation
        expected_old: List[str] = []
        new_chunk: List[str] = []
        for l in hunk[1:]:
            if l.startswith(" "):
                expected_old.append(l[1:])
                new_chunk.append(l[1:])
            elif l.startswith("-"):
                expected_old.append(l[1:])
            elif l.startswith("+"):
                new_chunk.append(l[1:])

        # Verify context match
        match_len = len(expected_old)
        if idx + match_len <= len(res):
            current_slice = res[idx : idx + match_len]
            if current_slice == expected_old:
                res[idx : idx + match_len] = new_chunk
                continue

        # If direct index match fails, scan nearby lines for context match
        found_idx = -1
        search_radius = 20
        start_search = max(0, idx - search_radius)
        end_search = min(len(res) - match_len + 1, idx + search_radius)
        for cand in range(start_search, max(start_search + 1, end_search)):
            if res[cand : cand + match_len] == expected_old:
                found_idx = cand
                break

        if found_idx != -1:
            res[found_idx : found_idx + match_len] = new_chunk
        else:
            return res, False, f"Hunk context failed: {hunk_header.strip()}"

    return res, True, ""


def apply_patch(
    session: RollbackSession,
    patch_content_or_path: str | Path,
    dry_run: bool = False,
    abort_on_failure: bool = True,
) -> PatchApplyResult:
    """
    Applies a unified patch to the live workspace files with atomic pre-patch checkpointing.
    """
    p_path = Path(patch_content_or_path)
    if p_path.exists() and p_path.is_file():
        patch_text = p_path.read_text(encoding="utf-8", errors="replace")
    else:
        patch_text = str(patch_content_or_path)

    # Parse file patches
    file_patches: Dict[str, List[List[str]]] = {}
    current_file: Optional[str] = None
    current_hunk: Optional[List[str]] = None

    for line in patch_text.splitlines(keepends=True):
        if line.startswith("diff --git"):
            # diff --git a/path b/path
            parts = line.strip().split()
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    current_file = b_path[2:]
                else:
                    current_file = b_path
                file_patches[current_file] = []
                current_hunk = None
        elif line.startswith("+++ "):
            # +++ b/path
            clean = line.strip()[4:].strip()
            if clean.startswith("b/"):
                clean = clean[2:]
            if clean != "/dev/null":
                current_file = clean
                if current_file not in file_patches:
                    file_patches[current_file] = []
                current_hunk = None
        elif line.startswith("@@"):
            if current_file:
                current_hunk = [line]
                file_patches[current_file].append(current_hunk)
        elif current_hunk is not None:
            current_hunk.append(line)

    if not file_patches:
        return PatchApplyResult(
            status="failed",
            message="No valid unified diff hunks found in patch.",
        )

    files_affected: List[str] = []
    failed_hunks: List[Dict[str, Any]] = []
    staged_contents: Dict[str, List[str]] = {}

    for rel_path, hunks in file_patches.items():
        file_path = session.root / rel_path
        orig_lines: List[str] = []
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                orig_lines = f.readlines()

        new_lines, success, err_msg = _apply_hunks_to_lines(orig_lines, hunks)
        if not success:
            failed_hunks.append({"file": rel_path, "error": err_msg})
        else:
            files_affected.append(rel_path)
            staged_contents[rel_path] = new_lines

    if failed_hunks and abort_on_failure:
        return PatchApplyResult(
            status="failed",
            failed_hunks=failed_hunks,
            message=f"Patch application failed on {len(failed_hunks)} file(s).",
        )

    if dry_run:
        return PatchApplyResult(
            status="dry_run_success" if not failed_hunks else "failed",
            files_affected=files_affected,
            failed_hunks=failed_hunks,
            message=f"Dry-run simulation: {len(files_affected)} file(s) would be modified.",
        )

    # Save pre-patch checkpoint
    pre_cp = session.checkpoint(label="pre_patch_apply")

    # Apply staged contents to disk
    for rel_path, lines in staged_contents.items():
        dest = session.root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.writelines(lines)

    # Save post-patch checkpoint
    post_cp = session.checkpoint(label="post_patch_apply")

    return PatchApplyResult(
        status="applied",
        files_affected=files_affected,
        failed_hunks=failed_hunks,
        pre_checkpoint=pre_cp.id,
        post_checkpoint=post_cp.id,
        message=f"Applied patch successfully to {len(files_affected)} file(s).",
    )
