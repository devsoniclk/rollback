import pytest
from pathlib import Path
from rollback.core import RollbackSession
from rollback.merge import MergeConflictError, MergeResult


def test_merge_fast_forward(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f = tmp_path / "app.py"
    f.write_text("v1_base\n")
    cp_base = session.checkpoint("base")

    session.create_branch("feature", from_checkpoint=cp_base.id)
    session.checkout("feature")

    f.write_text("v2_feature\n")
    session.checkpoint("feature_work")

    # Switch back to main (which is still at cp_base)
    session.checkout("main")
    assert f.read_text() == "v1_base\n"

    # Merge feature into main -> should be fast-forward
    res = session.merge_branch("feature")
    assert res.status == "fast-forward"
    assert f.read_text() == "v2_feature\n"


def test_merge_clean_distinct_files(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f1 = tmp_path / "f1.py"
    f2 = tmp_path / "f2.py"
    f1.write_text("f1_base\n")
    f2.write_text("f2_base\n")
    session.checkpoint("base")

    session.create_branch("feature")
    session.checkout("feature")
    f2.write_text("f2_modified_in_feature\n")
    session.checkpoint("feature_f2")

    session.checkout("main")
    f1.write_text("f1_modified_in_main\n")
    session.checkpoint("main_f1")

    # Merge feature into main
    res = session.merge_branch("feature")
    assert res.status == "clean"
    assert not res.has_conflicts
    assert f1.read_text() == "f1_modified_in_main\n"
    assert f2.read_text() == "f2_modified_in_feature\n"


def test_merge_non_overlapping_lines(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f = tmp_path / "module.py"
    f.write_text("line1\nline2\nline3\nline4\n")
    session.checkpoint("base")

    session.create_branch("feature")
    session.checkout("feature")
    f.write_text("line1\nline2\nline3\nline4_modified_in_feature\n")
    session.checkpoint("feature_change")

    session.checkout("main")
    f.write_text("line1_modified_in_main\nline2\nline3\nline4\n")
    session.checkpoint("main_change")

    # Merge feature into main
    res = session.merge_branch("feature")
    assert res.status == "clean"
    assert not res.has_conflicts
    content = f.read_text()
    assert "line1_modified_in_main" in content
    assert "line4_modified_in_feature" in content


def test_merge_conflict_markers(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f = tmp_path / "conflict.txt"
    f.write_text("base text\n")
    session.checkpoint("base")

    session.create_branch("feature")
    session.checkout("feature")
    f.write_text("feature conflicting text\n")
    session.checkpoint("feature_change")

    session.checkout("main")
    f.write_text("main conflicting text\n")
    session.checkpoint("main_change")

    # Merge without abort -> should insert conflict markers
    res = session.merge_branch("feature", abort_on_conflict=False)
    assert res.status == "conflict"
    assert res.has_conflicts
    assert "conflict.txt" in res.conflicting_files

    content = f.read_text()
    assert "<<<<<<< HEAD (main)" in content
    assert "main conflicting text" in content
    assert "=======" in content
    assert "feature conflicting text" in content
    assert ">>>>>>> feature" in content


def test_merge_abort_on_conflict(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f = tmp_path / "conflict.txt"
    f.write_text("base text\n")
    session.checkpoint("base")

    session.create_branch("feature")
    session.checkout("feature")
    f.write_text("feature conflicting text\n")
    session.checkpoint("feature_change")

    session.checkout("main")
    f.write_text("main conflicting text\n")
    session.checkpoint("main_change")

    with pytest.raises(MergeConflictError):
        session.merge_branch("feature", abort_on_conflict=True)

    # Verify workspace reverted back to main state
    assert f.read_text() == "main conflicting text\n"


def test_merge_already_up_to_date(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f = tmp_path / "file.py"
    f.write_text("content\n")
    session.checkpoint("base")

    session.create_branch("feature")
    res = session.merge_branch("feature")
    assert res.status == "up-to-date"
