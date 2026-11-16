import pytest
from pathlib import Path
from rollback.core import RevertPlan, RollbackSession


def test_revert_dry_run_plan(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f1 = tmp_path / "app.py"
    f1.write_text("print('initial')")
    cp = session.checkpoint("initial")

    # Mutate f1, create f2
    f1.write_text("print('corrupted')")
    f2 = tmp_path / "rogue.py"
    f2.write_text("rogue")

    # Generate dry-run plan
    plan = session.plan_revert("HEAD")
    assert isinstance(plan, RevertPlan)
    assert "app.py" in plan.files_to_restore
    assert "rogue.py" in plan.files_to_delete
    assert plan.total_changes == 2

    # Verify dry run did NOT touch disk
    assert f1.read_text() == "print('corrupted')"
    assert f2.exists()

    # Dry run via revert(dry_run=True)
    plan_via_revert = session.revert("HEAD", dry_run=True)
    assert isinstance(plan_via_revert, RevertPlan)
    assert plan_via_revert.total_changes == 2

    # Now execute actual revert
    actions = session.revert("HEAD", dry_run=False)
    assert isinstance(actions, dict)
    assert f1.read_text() == "print('initial')"
    assert not f2.exists()
