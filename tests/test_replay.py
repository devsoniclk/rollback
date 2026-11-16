from __future__ import annotations

import tempfile
from pathlib import Path
from rollback.core import RollbackSession
from rollback.replay import ReplayEngine, ReplayStep, ReplaySummary


def test_deterministic_replay_forward_and_backward() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        session = RollbackSession(root_dir=str(root))

        target_file = root / "state.txt"

        # Step 1
        target_file.write_text("state_1")
        cp1 = session.checkpoint(label="step_1")
        session.tag(cp1.id, "v1")

        # Step 2
        target_file.write_text("state_2")
        cp2 = session.checkpoint(label="step_2")
        session.tag(cp2.id, "v2")

        # Step 3
        target_file.write_text("state_3")
        cp3 = session.checkpoint(label="step_3")
        session.tag(cp3.id, "v3")

        assert target_file.read_text() == "state_3"

        # Test dry-run replay to v1
        summary_dry = session.replay("v1", dry_run=True)
        assert summary_dry.success is True
        assert summary_dry.target_checkpoint_id == cp1.id
        # In dry run, disk is untouched
        assert target_file.read_text() == "state_3"

        # Test live replay to v1
        callback_steps = []
        summary_live = session.replay("v1", step_by_step=True, on_step=lambda st: callback_steps.append(st))
        assert summary_live.success is True
        assert len(callback_steps) > 0
        assert target_file.read_text() == "state_1"

        # Test replaying forward to v2
        summary_fwd = session.replay("v2", step_by_step=False)
        assert summary_fwd.success is True
        assert target_file.read_text() == "state_2"

        # Test replaying to HEAD
        summary_head = session.replay("v3")
        assert summary_head.success is True
        assert target_file.read_text() == "state_3"


def test_replay_engine_path_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        session = RollbackSession(root_dir=str(root))

        (root / "a.txt").write_text("a")
        cp1 = session.checkpoint(label="1")
        (root / "b.txt").write_text("b")
        cp2 = session.checkpoint(label="2")
        (root / "c.txt").write_text("c")
        cp3 = session.checkpoint(label="3")

        engine = ReplayEngine(session)
        # Moving forward
        path_fwd = engine.resolve_path(cp1.id, cp3.id)
        assert path_fwd == [cp2.id, cp3.id]

        # Moving backward
        path_bwd = engine.resolve_path(cp3.id, cp1.id)
        assert path_bwd == [cp2.id, cp1.id]
