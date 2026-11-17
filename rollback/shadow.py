"""
rollback.shadow: Ephemeral Worktree Shadow Clones & Multi-Hypothesis Explorer for AI coding agents.

Enables agents to spawn isolated copy-on-write workspaces to explore and evaluate competing code hypotheses
(e.g., bug fixes, refactors, alternative library implementations) in parallel without mutating the main workspace.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

if TYPE_CHECKING:
    from .core import Checkpoint, RollbackSession


@dataclass
class ShadowHypothesis:
    hypothesis_id: str
    description: str
    created_at: float
    base_checkpoint_id: str
    shadow_path: str
    status: str = "active"  # "active", "evaluated", "promoted", "discarded"
    score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ShadowHypothesis:
        return cls(**data)


@dataclass
class ShadowEvaluationResult:
    hypothesis_id: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    score: float
    duration_ms: float
    delta_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "score": round(self.score, 4),
            "duration_ms": round(self.duration_ms, 2),
            "delta_files": self.delta_files,
        }


class ShadowCloneManager:
    """
    Manages lightweight ephemeral shadow clones under `.rollback/shadows/`.
    Allows agents to test multiple candidate solutions, run sandboxed tests,
    and atomically promote the winning hypothesis into the active workspace.
    """

    IGNORE_PATTERNS = {".git", ".rollback", "__pycache__", ".pytest_cache", "node_modules", "venv", ".venv"}

    def __init__(self, session: RollbackSession):
        self.session = session
        self.root_dir = Path(session.root_dir).resolve()
        self.shadows_dir = self.session.journal_dir / "shadows"
        self.shadows_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.shadows_dir / "hypotheses.json"
        self._hypotheses: Dict[str, ShadowHypothesis] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        if not self.metadata_file.exists():
            return
        try:
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._hypotheses = {h["hypothesis_id"]: ShadowHypothesis.from_dict(h) for h in data.get("hypotheses", [])}
        except Exception:
            self._hypotheses = {}

    def _save_metadata(self) -> None:
        data = {"hypotheses": [h.to_dict() for h in self._hypotheses.values()]}
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def create_hypothesis(
        self,
        hypothesis_id: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ShadowHypothesis:
        """Create an ephemeral shadow clone copying current workspace files."""
        # Ensure a base checkpoint exists
        base_cp = self.session.create_checkpoint(f"shadow_base_{hypothesis_id}")

        shadow_path = self.shadows_dir / hypothesis_id
        if shadow_path.exists():
            shutil.rmtree(shadow_path)
        shadow_path.mkdir(parents=True, exist_ok=True)

        # Copy workspace files excluding ignored directories
        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_PATTERNS]
            for file in files:
                src_file = Path(root, file)
                rel_path = src_file.relative_to(self.root_dir)
                dest_file = shadow_path / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)

        hyp = ShadowHypothesis(
            hypothesis_id=hypothesis_id,
            description=description,
            created_at=time.time(),
            base_checkpoint_id=base_cp.id,
            shadow_path=str(shadow_path),
            status="active",
            metadata=metadata or {},
        )
        self._hypotheses[hypothesis_id] = hyp
        self._save_metadata()
        return hyp

    def get_hypothesis(self, hypothesis_id: str) -> Optional[ShadowHypothesis]:
        return self._hypotheses.get(hypothesis_id)

    def list_hypotheses(self) -> List[ShadowHypothesis]:
        return list(self._hypotheses.values())

    def evaluate_hypothesis(
        self,
        hypothesis_id: str,
        test_cmd: str,
        timeout_sec: float = 30.0,
    ) -> ShadowEvaluationResult:
        """Runs test_cmd inside the shadow clone directory and measures results."""
        hyp = self._hypotheses.get(hypothesis_id)
        if not hyp:
            raise KeyError(f"Shadow hypothesis '{hypothesis_id}' not found")

        shadow_path = Path(hyp.shadow_path)
        if not shadow_path.exists():
            raise FileNotFoundError(f"Shadow directory '{shadow_path}' does not exist")

        start = time.time()
        try:
            res = subprocess.run(
                test_cmd,
                shell=True,
                cwd=str(shadow_path),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            exit_code = res.returncode
            stdout = res.stdout
            stderr = res.stderr
        except subprocess.TimeoutExpired:
            exit_code = 124
            stdout = ""
            stderr = f"Evaluation timed out after {timeout_sec}s"
        except Exception as ex:
            exit_code = 1
            stdout = ""
            stderr = str(ex)

        duration_ms = (time.time() - start) * 1000.0
        passed = exit_code == 0
        score = 1.0 if passed else 0.0

        # Calculate delta files
        delta_files = []
        for root, dirs, files in os.walk(shadow_path):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_PATTERNS]
            for file in files:
                sh_file = Path(root, file)
                rel = sh_file.relative_to(shadow_path)
                orig_file = self.root_dir / rel
                if not orig_file.exists():
                    delta_files.append(f"+ {rel}")
                else:
                    if sh_file.read_bytes() != orig_file.read_bytes():
                        delta_files.append(f"M {rel}")

        hyp.status = "evaluated"
        hyp.score = score
        self._save_metadata()

        return ShadowEvaluationResult(
            hypothesis_id=hypothesis_id,
            passed=passed,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            score=score,
            duration_ms=duration_ms,
            delta_files=delta_files,
        )

    def promote_hypothesis(
        self,
        hypothesis_id: str,
        create_checkpoint: bool = True,
    ) -> Dict[str, Any]:
        """Promote files from the shadow clone into the primary workspace."""
        hyp = self._hypotheses.get(hypothesis_id)
        if not hyp:
            raise KeyError(f"Shadow hypothesis '{hypothesis_id}' not found")

        shadow_path = Path(hyp.shadow_path)
        if not shadow_path.exists():
            raise FileNotFoundError(f"Shadow path does not exist: {shadow_path}")

        promoted_files = []
        # Copy shadow files over main workspace
        for root, dirs, files in os.walk(shadow_path):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_PATTERNS]
            for file in files:
                sh_file = Path(root, file)
                rel = sh_file.relative_to(shadow_path)
                dest = self.root_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sh_file, dest)
                promoted_files.append(str(rel))

        cp_id = None
        if create_checkpoint:
            cp = self.session.create_checkpoint(
                label=f"promote_shadow_{hypothesis_id}",
                tag=f"hyp-{hypothesis_id}",
            )
            cp_id = cp.id

        hyp.status = "promoted"
        self._save_metadata()

        return {
            "hypothesis_id": hypothesis_id,
            "promoted_files_count": len(promoted_files),
            "promoted_files": promoted_files,
            "checkpoint_id": cp_id,
            "status": "promoted",
        }

    def discard_hypothesis(self, hypothesis_id: str) -> bool:
        """Removes the shadow clone directory and cleans up metadata."""
        hyp = self._hypotheses.get(hypothesis_id)
        if not hyp:
            return False

        shadow_path = Path(hyp.shadow_path)
        if shadow_path.exists():
            shutil.rmtree(shadow_path, ignore_errors=True)

        hyp.status = "discarded"
        self._save_metadata()
        return True
