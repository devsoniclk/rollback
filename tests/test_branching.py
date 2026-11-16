import pytest
from pathlib import Path
from rollback.core import RollbackSession


def test_timeline_branching(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    # Base checkpoint
    f = tmp_path / "code.py"
    f.write_text("v1_main")
    cp_base = session.checkpoint("base")

    # Create experimental branch
    session.create_branch("experiment", from_checkpoint=cp_base.id)
    branches = session.list_branches()
    assert "experiment" in branches["branches"]
    assert branches["current"] == "main"

    # Switch to experiment
    session.checkout("experiment")
    assert session.current_branch == "experiment"

    # Mutate on experiment branch
    f.write_text("v2_experiment")
    session.checkpoint("experiment_feature")

    # Switch back to main
    session.checkout("main")
    assert session.current_branch == "main"
    assert f.read_text() == "v1_main"

    # Switch back to experiment
    session.checkout("experiment")
    assert session.current_branch == "experiment"
    assert f.read_text() == "v2_experiment"
