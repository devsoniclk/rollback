import os
import shutil
import tempfile
from pathlib import Path
import pytest

from rollback.core import RollbackSession
from rollback.security import SecurityPolicy
from rollback.selective import SelectiveRevertEngine, SelectiveRevertError


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_rollback_selective_")
    yield Path(tmp_dir)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_selective_single_file_revert(temp_workspace):
    session = RollbackSession(root_dir=str(temp_workspace))

    f1 = temp_workspace / "module_a.py"
    f2 = temp_workspace / "module_b.py"

    f1.write_text("def a(): return 1\n")
    f2.write_text("def b(): return 1\n")
    cp1 = session.checkpoint(label="c1_initial")

    # Modify both files
    f1.write_text("def a(): return 999\n")
    f2.write_text("def b(): return 999\n")
    cp2 = session.checkpoint(label="c2_modified")

    # Selectively revert ONLY module_a.py back to c1
    engine = SelectiveRevertEngine(session)
    plan = engine.plan_selective_revert(cp1.id, ["module_a.py"])
    assert plan.total_changes == 1
    assert "module_a.py" in plan.files_to_restore
    assert "module_b.py" not in plan.files_to_restore

    res = engine.revert_files(cp1.id, ["module_a.py"])
    assert res["status"] == "success"
    assert res["files_affected"]["module_a.py"] == "restore"

    # module_a is reverted, module_b is unchanged
    assert f1.read_text() == "def a(): return 1\n"
    assert f2.read_text() == "def b(): return 999\n"


def test_selective_resurrect_and_delete(temp_workspace):
    session = RollbackSession(root_dir=str(temp_workspace))

    f_resurrect = temp_workspace / "resurrect.txt"
    f_resurrect.write_text("I existed in c1\n")
    cp1 = session.checkpoint(label="c1")

    # Delete resurrect.txt, create untracked.txt
    f_resurrect.unlink()
    f_untracked = temp_workspace / "untracked.txt"
    f_untracked.write_text("Created after c1\n")
    cp2 = session.checkpoint(label="c2")

    engine = SelectiveRevertEngine(session)
    plan = engine.plan_selective_revert(cp1.id, ["resurrect.txt", "untracked.txt"])
    assert "resurrect.txt" in plan.files_to_resurrect
    assert "untracked.txt" in plan.files_to_delete

    # Execute selective revert
    res = engine.revert_files(cp1.id, ["resurrect.txt", "untracked.txt"])
    assert f_resurrect.exists()
    assert f_resurrect.read_text() == "I existed in c1\n"
    assert not f_untracked.exists()


def test_selective_dry_run_and_skip(temp_workspace):
    session = RollbackSession(root_dir=str(temp_workspace))

    f1 = temp_workspace / "file1.txt"
    f1.write_text("identical content\n")
    cp1 = session.checkpoint(label="c1")

    # File unchanged
    engine = SelectiveRevertEngine(session)
    plan = engine.plan_selective_revert(cp1.id, ["file1.txt"])
    assert plan.total_changes == 0
    assert "file1.txt" in plan.files_skipped

    # Dry run
    res = engine.revert_files(cp1.id, ["file1.txt"], dry_run=True)
    assert res["dry_run"] is True
    assert res["plan"]["total_changes"] == 0


def test_cherry_pick_across_branches(temp_workspace):
    session = RollbackSession(root_dir=str(temp_workspace))

    f_base = temp_workspace / "feature.py"
    f_base.write_text("initial\n")
    cp_main = session.checkpoint(label="main_head")

    # Create and checkout experiment branch
    session.create_branch("experiment")
    session.checkout("experiment")

    f_base.write_text("experimental feature code\n")
    cp_exp = session.checkpoint(label="experiment_done")

    # Switch back to main
    session.checkout("main")
    assert f_base.read_text() == "initial\n"

    # Cherry-pick feature.py from experiment branch
    session.cherry_pick(cp_exp.id, ["feature.py"])
    assert f_base.read_text() == "experimental feature code\n"


def test_selective_security_containment(temp_workspace):
    policy = SecurityPolicy(allowed_root=temp_workspace)
    session = RollbackSession(root_dir=str(temp_workspace), security_policy=policy)
    engine = SelectiveRevertEngine(session)

    f = temp_workspace / "safe.txt"
    f.write_text("hello\n")
    cp = session.checkpoint(label="c1")

    # Traversal attempt
    with pytest.raises(Exception):
        engine.plan_selective_revert(cp.id, ["../outside.txt"])
