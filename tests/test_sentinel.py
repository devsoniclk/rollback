import tempfile
import shutil
from pathlib import Path
import pytest

from rollback.core import RollbackSession
from rollback.sentinel import WorkspaceSentinel, DriftDetectedError


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_rollback_sentinel_")
    yield Path(tmp_dir)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_sentinel_clean_workspace(temp_workspace):
    session = RollbackSession(root_dir=str(temp_workspace))
    f = temp_workspace / "main.py"
    f.write_text("print('hello')\n")
    session.checkpoint(label="initial")

    sentinel = WorkspaceSentinel(session)
    report = sentinel.scan_drift()
    assert report.is_dirty is False
    assert report.total_changes == 0
    assert report.modified_files == []
    assert report.untracked_files == []
    assert report.deleted_files == []

    # Clean assertion passes
    sentinel.assert_clean()


def test_sentinel_drift_detection(temp_workspace):
    session = RollbackSession(root_dir=str(temp_workspace))
    f_mod = temp_workspace / "modified.py"
    f_del = temp_workspace / "deleted.py"

    f_mod.write_text("version 1\n")
    f_del.write_text("to be deleted\n")
    cp = session.checkpoint(label="c1")

    # Mutate workspace: modify, delete, and add untracked
    f_mod.write_text("version 2 modified with more content\n")
    f_del.unlink()
    f_new = temp_workspace / "untracked.py"
    f_new.write_text("new untracked file\n")

    sentinel = WorkspaceSentinel(session)
    report = sentinel.scan_drift()
    assert report.is_dirty is True
    assert report.total_changes == 3
    assert "modified.py" in report.modified_files
    assert "deleted.py" in report.deleted_files
    assert "untracked.py" in report.untracked_files
    assert report.total_delta_bytes > 0

    with pytest.raises(DriftDetectedError):
        sentinel.assert_clean()


def test_sentinel_auto_checkpoint(temp_workspace):
    session = RollbackSession(root_dir=str(temp_workspace))
    f = temp_workspace / "app.py"
    f.write_text("initial\n")
    session.checkpoint(label="c1")

    sentinel = WorkspaceSentinel(session)
    # No changes: returns None
    assert sentinel.auto_checkpoint() is None

    # Make 2 changes
    f.write_text("modified code\n")
    (temp_workspace / "extra.txt").write_text("extra\n")

    cp_auto = sentinel.auto_checkpoint(trigger_label="test_auto", min_changes=2, tag="auto_v1")
    assert cp_auto is not None
    assert "test_auto" in cp_auto.label
    assert session.tags.get("auto_v1") == cp_auto.id

    # Now workspace is clean relative to new HEAD
    assert sentinel.scan_drift().is_dirty is False
