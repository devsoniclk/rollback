"""
agent-witness integration for rollback.
Enables automatic cryptographic Merkle attestation of rollback checkpoints,
diff summaries, and transactional leases.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from .core import Checkpoint, RollbackSession

try:
    import agent_witness
    from agent_witness.models import StepType
    from agent_witness.receipt import WitnessEngine
    HAS_WITNESS = True
except ImportError:
    HAS_WITNESS = False


def attest_checkpoint(
    checkpoint: Checkpoint,
    engine: Optional[Any] = None,
    agent_id: str = "rollback-agent",
) -> Dict[str, Any]:
    """
    Attests a rollback checkpoint into an agent-witness deliberation step or receipt.
    """
    eng = engine or (WitnessEngine(agent_id=agent_id) if HAS_WITNESS else None)
    step_desc = f"Rollback Checkpoint: {checkpoint.id} ({checkpoint.label})"
    tracked = list(checkpoint.files.keys()) if hasattr(checkpoint, "files") else getattr(checkpoint, "tracked_files", [])
    meta = {
        "checkpoint_id": checkpoint.id,
        "label": checkpoint.label,
        "timestamp": checkpoint.timestamp,
        "parent_id": checkpoint.parent_id,
        "file_count": len(tracked),
        "tracked_files": sorted(tracked),
    }
    if eng:
        step = eng.record_step(StepType.TOOL_RESULT, step_desc, metadata=meta)
        return {"attested": True, "step_index": step.index, "checkpoint_id": checkpoint.id}
    else:
        return {"attested": False, "checkpoint_id": checkpoint.id, "reason": "agent-witness not installed"}


def attest_diff_summary(
    diff_summary: Any,
    engine: Optional[Any] = None,
    agent_id: str = "rollback-agent",
) -> Dict[str, Any]:
    """
    Attests a file diff summary into an agent-witness deliberation step.
    """
    eng = engine or (WitnessEngine(agent_id=agent_id) if HAS_WITNESS else None)
    files_changed = getattr(diff_summary, "total_files_changed", getattr(diff_summary, "files_changed", 0))
    files = getattr(diff_summary, "files", [])
    insertions = sum(getattr(f, "lines_added", 0) for f in files) if files else getattr(diff_summary, "insertions", 0)
    deletions = sum(getattr(f, "lines_deleted", 0) for f in files) if files else getattr(diff_summary, "deletions", 0)
    modified_files = [getattr(f, "path", str(f)) for f in files] if files else getattr(diff_summary, "modified_files", [])

    step_desc = f"Rollback Diff: {files_changed} changed, +{insertions}/-{deletions}"
    meta = {
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
        "modified_files": modified_files,
    }
    if eng:
        step = eng.record_step(StepType.TOOL_RESULT, step_desc, metadata=meta)
        return {"attested": True, "step_index": step.index, "files_changed": files_changed}
    else:
        return {"attested": False, "reason": "agent-witness not installed"}
