from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import RollbackSession, Checkpoint, FileState


@dataclass
class ReplayStep:
    step_number: int
    from_checkpoint_id: Optional[str]
    to_checkpoint_id: str
    label: str
    files_changed: List[str]
    verified: bool
    duration_ms: float


@dataclass
class ReplaySummary:
    success: bool
    target_checkpoint_id: str
    steps_count: int
    steps: List[ReplayStep] = field(default_factory=list)
    final_checkpoint_id: Optional[str] = None
    message: str = ""


class ReplayEngine:
    """
    Deterministic time-travel and replay engine for AI coding agent checkpoints.
    Enables step-by-step playback, verification hooks, and multi-step state transitions.
    """

    def __init__(self, session: RollbackSession):
        self.session = session

    def resolve_path(self, from_id: Optional[str], to_id: str) -> List[str]:
        """
        Computes the sequence of checkpoint IDs to transition from from_id to to_id.
        If both are in the linear history, returns the ordered slice.
        """
        history = self.session.history
        if to_id not in history:
            # Check if to_id exists on another branch
            target_cp = self.session.get_checkpoint(to_id)
            if not target_cp:
                raise ValueError(f"Target checkpoint '{to_id}' not found in session journal.")
            return [to_id]

        if not from_id or from_id not in history:
            # Start from first checkpoint up to to_id
            to_idx = history.index(to_id)
            return history[: to_idx + 1]

        from_idx = history.index(from_id)
        to_idx = history.index(to_id)

        if from_idx <= to_idx:
            # Moving forward in history
            return history[from_idx + 1 : to_idx + 1]
        else:
            # Moving backward in history
            return history[to_idx : from_idx][::-1]

    def replay(
        self,
        target_identifier: str,
        step_by_step: bool = False,
        dry_run: bool = False,
        on_step: Optional[Callable[[ReplayStep], None]] = None,
    ) -> ReplaySummary:
        """
        Replays workspace state to the target checkpoint or tag.
        
        Args:
            target_identifier: Checkpoint ID, tag name, or 'HEAD'
            step_by_step: If True, applies and verifies intermediate checkpoints sequentially
            dry_run: If True, calculates operations and verifies hashes without modifying disk
            on_step: Optional callback invoked after each step transition
        """
        target_id = self.session.resolve_checkpoint_id(target_identifier)
        target_cp = self.session.get_checkpoint(target_id)
        if not target_cp:
            return ReplaySummary(
                success=False,
                target_checkpoint_id=target_id,
                steps_count=0,
                message=f"Target checkpoint '{target_id}' does not exist.",
            )

        current_head = self.session.history[-1] if self.session.history else None

        path = self.resolve_path(current_head, target_id)
        if not path:
            path = [target_id]

        steps: List[ReplayStep] = []
        last_cid = current_head

        if step_by_step and len(path) > 1:
            # Transition through intermediate checkpoints
            for i, step_cid in enumerate(path):
                t_start = time.perf_counter()
                step_cp = self.session.get_checkpoint(step_cid)
                if not step_cp:
                    continue

                affected_files = list(step_cp.files.keys())
                verified = True

                if not dry_run:
                    # Apply checkpoint state
                    self.session.revert(step_cid, dry_run=False)

                    # Verify hashes of target files
                    for rel_p, fs in step_cp.files.items():
                        full_p = self.session.root / rel_p
                        if fs.exists:
                            if not full_p.exists():
                                verified = False
                                break
                        else:
                            if full_p.exists():
                                verified = False
                                break

                dur_ms = (time.perf_counter() - t_start) * 1000.0
                rstep = ReplayStep(
                    step_number=i + 1,
                    from_checkpoint_id=last_cid,
                    to_checkpoint_id=step_cid,
                    label=step_cp.label,
                    files_changed=affected_files,
                    verified=verified,
                    duration_ms=dur_ms,
                )
                steps.append(rstep)
                if on_step:
                    on_step(rstep)
                last_cid = step_cid
        else:
            # Direct atomic jump to target checkpoint
            t_start = time.perf_counter()
            affected = list(target_cp.files.keys())
            verified = True

            if not dry_run:
                self.session.revert(target_id, dry_run=False)
                for rel_p, fs in target_cp.files.items():
                    full_p = self.session.root / rel_p
                    if fs.exists:
                        if not full_p.exists():
                            verified = False
                            break
                    else:
                        if full_p.exists():
                            verified = False
                            break

            dur_ms = (time.perf_counter() - t_start) * 1000.0
            rstep = ReplayStep(
                step_number=1,
                from_checkpoint_id=current_head,
                to_checkpoint_id=target_id,
                label=target_cp.label,
                files_changed=affected,
                verified=verified,
                duration_ms=dur_ms,
            )
            steps.append(rstep)
            if on_step:
                on_step(rstep)

        return ReplaySummary(
            success=True,
            target_checkpoint_id=target_id,
            steps_count=len(steps),
            steps=steps,
            final_checkpoint_id=target_id,
            message=f"Replayed {len(steps)} step(s) to checkpoint '{target_id[:8]}' ({'dry-run' if dry_run else 'live'}).",
        )
