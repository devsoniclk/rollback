import pytest
from pathlib import Path
from rollback.core import RollbackSession
from rollback.git_bridge import GitBridge


def test_git_bridge_status_non_git(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))
    bridge = GitBridge(session)
    status = bridge.get_status()
    assert not status.is_git_repo


def test_git_bridge_export_and_import(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f = tmp_path / "app.py"
    f.write_text("print('version 1')\n")
    session.checkpoint("Initial commit")

    f.write_text("print('version 2')\n")
    session.checkpoint("Second commit")

    bridge = GitBridge(session)
    # Export checkpoints to git
    res = bridge.export_to_git(git_branch="main")
    assert res["status"] == "success"
    assert res["exported_commits"] >= 1

    status = bridge.get_status()
    assert status.is_git_repo
    assert status.git_head is not None

    # Test importing back into a clean rollback session
    session2 = RollbackSession(root_dir=str(tmp_path), journal_dir=str(tmp_path / ".rollback2"))
    bridge2 = GitBridge(session2)
    cids = bridge2.import_from_git(max_commits=5)
    assert len(cids) >= 1
