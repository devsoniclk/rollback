from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Checkpoint, RollbackSession


@dataclass
class GitSyncStatus:
    is_git_repo: bool
    git_head: Optional[str] = None
    git_branch: Optional[str] = None
    rollback_head: Optional[str] = None
    rollback_branch: str = "main"
    dirty_files: List[str] = field(default_factory=list)
    unexported_checkpoints: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_git_repo": self.is_git_repo,
            "git_head": self.git_head,
            "git_branch": self.git_branch,
            "rollback_head": self.rollback_head,
            "rollback_branch": self.rollback_branch,
            "dirty_files": self.dirty_files,
            "unexported_checkpoints": self.unexported_checkpoints,
        }


class GitBridge:
    """
    Bidirectional bridge between rollback micro-checkpoints and local Git commits.
    Enables selective export of agent checkpoints into Git history with co-author trailers,
    importing Git commits into rollback timelines, and live status synchronization.
    """

    def __init__(self, session: RollbackSession, git_dir: Optional[str | Path] = None):
        self.session = session
        self.git_dir = Path(git_dir).resolve() if git_dir else self.session.root

    def _run_git(self, args: List[str], env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        current_env = dict(os.environ)
        if env:
            current_env.update(env)
        return subprocess.run(
            ["git"] + args,
            cwd=str(self.git_dir),
            capture_output=True,
            text=True,
            env=current_env,
        )

    def is_git_repo(self) -> bool:
        r = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return r.returncode == 0 and r.stdout.strip() == "true"

    def init_git_repo(self) -> bool:
        if not self.is_git_repo():
            r = self._run_git(["init"])
            return r.returncode == 0
        return True

    def get_status(self) -> GitSyncStatus:
        if not self.is_git_repo():
            return GitSyncStatus(
                is_git_repo=False,
                rollback_head=self.session.history[-1] if self.session.history else None,
                rollback_branch=self.session.current_branch,
            )

        # Get HEAD commit
        r_head = self._run_git(["rev-parse", "HEAD"])
        git_head = r_head.stdout.strip() if r_head.returncode == 0 else None

        # Get current branch
        r_br = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        git_br = r_br.stdout.strip() if r_br.returncode == 0 else None

        # Check dirty files
        r_status = self._run_git(["status", "--porcelain"])
        dirty = [line.strip() for line in r_status.stdout.splitlines() if line.strip()]

        rb_head = self.session.history[-1] if self.session.history else None

        return GitSyncStatus(
            is_git_repo=True,
            git_head=git_head,
            git_branch=git_br,
            rollback_head=rb_head,
            rollback_branch=self.session.current_branch,
            dirty_files=dirty,
            unexported_checkpoints=len(self.session.history),
        )

    def export_to_git(
        self,
        branch: Optional[str] = None,
        git_branch: Optional[str] = None,
        author_name: str = "OpenBot",
        author_email: str = "335604065+openbotgit[bot]@users.noreply.github.com",
    ) -> Dict[str, Any]:
        """
        Exports the checkpoint chain for the given branch into sequential Git commits.
        Adds co-author trailer to each commit message.
        """
        self.init_git_repo()

        target_branch = branch if branch else self.session.current_branch
        head_cid = self.session.branches.get(target_branch)
        if not head_cid:
            if self.session.history:
                head_cid = self.session.history[-1]
            else:
                return {"exported_commits": 0, "message": "No checkpoints to export."}

        # Build chronological chain
        chain: List[str] = []
        curr: Optional[str] = head_cid
        while curr:
            chain.append(curr)
            cp = self.session.get_checkpoint(curr)
            curr = cp.parent_id if cp else None

        chain.reverse()

        # Switch / create git branch if specified
        if git_branch:
            self._run_git(["checkout", "-B", git_branch])

        exported_count = 0
        last_sha = ""

        # Configure git user if missing in isolated repo
        r_user = self._run_git(["config", "user.name"])
        if not r_user.stdout.strip():
            self._run_git(["config", "user.name", author_name])
            self._run_git(["config", "user.email", author_email])

        for cid in chain:
            cp = self.session.get_checkpoint(cid)
            if not cp:
                continue

            # Restore files of checkpoint to git worktree
            self.session.revert(cid)

            # Stage all files
            self._run_git(["add", "-A"])

            # Commit
            commit_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(cp.timestamp))
            env = {
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_AUTHOR_DATE": f"{commit_time} +0000",
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
                "GIT_COMMITTER_DATE": f"{commit_time} +0000",
            }
            msg = (
                f"{cp.label}\n\n"
                f"Rollback-Checkpoint: {cp.id}\n"
                f"Branch: {cp.branch}\n\n"
                f"Co-authored-by: OpenBot <335604065+openbotgit[bot]@users.noreply.github.com>"
            )

            # Check if there are changes to commit
            r_diff = self._run_git(["diff", "--cached", "--quiet"])
            if r_diff.returncode != 0:
                r_commit = self._run_git(["commit", "-m", msg], env=env)
                if r_commit.returncode == 0:
                    exported_count += 1

        r_head = self._run_git(["rev-parse", "HEAD"])
        if r_head.returncode == 0:
            last_sha = r_head.stdout.strip()

        return {
            "status": "success",
            "exported_commits": exported_count,
            "git_head": last_sha,
            "branch": target_branch,
            "message": f"Successfully exported {exported_count} checkpoint(s) to Git.",
        }

    def import_from_git(self, max_commits: int = 10) -> List[str]:
        """
        Imports recent Git commits into the rollback session as immutable checkpoints.
        """
        if not self.is_git_repo():
            raise ValueError(f"Directory '{self.git_dir}' is not a valid Git repository.")

        r_log = self._run_git(
            ["log", f"-n{max_commits}", "--reverse", "--format=%H|%s|%at"]
        )
        if r_log.returncode != 0:
            return []

        imported_ids: List[str] = []
        for line in r_log.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 3:
                sha, subject, timestamp_s = parts[0], parts[1], parts[2]
                tag_name = f"git_{sha[:7]}"
                # Create checkpoint
                cp = self.session.checkpoint(label=f"git: {subject}")
                self.session.tag(cp.id, tag_name)
                imported_ids.append(cp.id)

        return imported_ids
