"""
agent-witness integration for rollback.
Enables automatic cryptographic Merkle attestation of rollback checkpoints,
diff summaries, and transactional leases with zero-dependency standalone fallback.
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
    tracked = list(checkpoint.files.keys()) if hasattr(checkpoint, "files") and isinstance(checkpoint.files, dict) else []
    step_desc = f"Rollback Checkpoint: {checkpoint.id} ({checkpoint.label})"
    meta = {
        "checkpoint_id": checkpoint.id,
        "label": checkpoint.label,
        "timestamp": checkpoint.timestamp,
        "parent_id": getattr(checkpoint, "parent_id", None),
        "file_count": len(tracked),
        "tracked_files": sorted(tracked),
    }

    if engine is not None:
        step = engine.record_step(getattr(engine, "step_type", "tool_result"), step_desc, metadata=meta)
        idx = getattr(step, "index", 0)
        return {
            "attested": True,
            "step_index": idx,
            "checkpoint_id": checkpoint.id,
            "witness_installed": True,
        }

    if HAS_WITNESS:
        eng = WitnessEngine(agent_id=agent_id)
        step = eng.record_step(StepType.TOOL_RESULT, step_desc, metadata=meta)
        return {
            "attested": True,
            "step_index": step.index,
            "checkpoint_id": checkpoint.id,
            "witness_installed": True,
        }

    return {
        "attested": False,
        "checkpoint_id": checkpoint.id,
        "reason": "agent-witness not installed",
        "witness_installed": False,
    }


def attest_diff_summary(
    diff_summary: Any,
    engine: Optional[Any] = None,
    agent_id: str = "rollback-agent",
) -> Dict[str, Any]:
    """
    Attests a file diff summary into an agent-witness deliberation step.
    """
    files_changed = getattr(diff_summary, "total_files_changed", getattr(diff_summary, "files_changed", 0))
    ins = getattr(diff_summary, "total_insertions", getattr(diff_summary, "insertions", 0))
    dels = getattr(diff_summary, "total_deletions", getattr(diff_summary, "deletions", 0))

    step_desc = f"Rollback Diff: {files_changed} changed, +{ins}/-{dels}"
    meta = {
        "files_changed": files_changed,
        "insertions": ins,
        "deletions": dels,
        "modified_files": getattr(diff_summary, "modified_files", []),
    }

    if engine is not None:
        step = engine.record_step(getattr(engine, "step_type", "tool_result"), step_desc, metadata=meta)
        idx = getattr(step, "index", 0)
        return {
            "attested": True,
            "step_index": idx,
            "files_changed": files_changed,
            "witness_installed": True,
        }

    if HAS_WITNESS:
        eng = WitnessEngine(agent_id=agent_id)
        step = eng.record_step(StepType.TOOL_RESULT, step_desc, metadata=meta)
        return {
            "attested": True,
            "step_index": step.index,
            "files_changed": files_changed,
            "witness_installed": True,
        }

    return {
        "attested": False,
        "files_changed": files_changed,
        "reason": "agent-witness not installed",
        "witness_installed": False,
    }
