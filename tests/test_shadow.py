import sys
import tempfile
from pathlib import Path
import pytest
from rollback.core import RollbackSession
from rollback.shadow import ShadowCloneManager, ShadowEvaluationResult, ShadowHypothesis


def test_shadow_clone_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        # Create initial workspace file
        main_file = Path(tmpdir) / "calculator.py"
        main_file.write_text("def add(a, b): return a - b  # Buggy!\n")
        session.checkpoint("initial_buggy_state")

        manager = session.shadow_manager
        assert isinstance(manager, ShadowCloneManager)

        # 1. Create hypothesis
        hyp = manager.create_hypothesis("fix-subtraction", description="Fix inverted operator in add()")
        assert isinstance(hyp, ShadowHypothesis)
        assert hyp.hypothesis_id == "fix-subtraction"
        assert hyp.status == "active"
        shadow_dir = Path(hyp.shadow_path)
        assert shadow_dir.exists()
        assert (shadow_dir / "calculator.py").exists()

        # 2. Modify inside shadow clone
        (shadow_dir / "calculator.py").write_text("def add(a, b): return a + b  # Fixed!\n")
        (shadow_dir / "new_helper.py").write_text("# new helper module\n")
        # Main workspace remains unmutated
        assert "return a - b" in main_file.read_text()

        # 3. Evaluate hypothesis with passing command
        cmd = f'"{sys.executable}" -c "import calculator; assert calculator.add(2, 3) == 5"'
        eval_res = manager.evaluate_hypothesis("fix-subtraction", test_cmd=cmd, timeout_sec=10.0)
        assert isinstance(eval_res, ShadowEvaluationResult)
        assert eval_res.passed is True
        assert eval_res.score == 1.0
        assert eval_res.exit_code == 0
        assert any("calculator.py" in d for d in eval_res.delta_files)
        assert any("new_helper.py" in d for d in eval_res.delta_files)

        # 4. List hypotheses
        hyps = manager.list_hypotheses()
        assert len(hyps) == 1
        assert hyps[0].status == "evaluated"
        assert hyps[0].score == 1.0

        # 5. Promote hypothesis
        promo = manager.promote_hypothesis("fix-subtraction", create_checkpoint=True)
        assert promo["status"] == "promoted"
        assert promo["promoted_files_count"] >= 2
        assert "return a + b" in main_file.read_text()
        assert (Path(tmpdir) / "new_helper.py").exists()
        assert promo["checkpoint_id"] is not None

        # 6. Discard hypothesis
        hyp2 = manager.create_hypothesis("experimental", description="To discard")
        assert Path(hyp2.shadow_path).exists()
        discarded = manager.discard_hypothesis("experimental")
        assert discarded is True
        assert not Path(hyp2.shadow_path).exists()


def test_shadow_clone_error_cases_and_persistence() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        (Path(tmpdir) / "app.py").write_text("x = 1\n")
        session.checkpoint("base")

        manager = session.shadow_manager

        # Non-existent hypothesis evaluate
        with pytest.raises(KeyError):
            manager.evaluate_hypothesis("ghost-hyp", test_cmd="exit 0")

        # Non-existent hypothesis promote
        with pytest.raises(KeyError):
            manager.promote_hypothesis("ghost-hyp")

        # Non-existent hypothesis discard returns False
        assert manager.discard_hypothesis("ghost-hyp") is False

        # Timeout handling
        manager.create_hypothesis("timeout-hyp")
        eval_res = manager.evaluate_hypothesis(
            "timeout-hyp",
            test_cmd=f'"{sys.executable}" -c "import time; time.sleep(5)"',
            timeout_sec=0.2,
        )
        assert eval_res.passed is False
        assert eval_res.exit_code == 124
        assert "timed out" in eval_res.stderr

        # Persistence across fresh manager reload
        new_manager = ShadowCloneManager(session)
        assert new_manager.get_hypothesis("timeout-hyp") is not None
        assert new_manager.get_hypothesis("timeout-hyp").status == "evaluated"
