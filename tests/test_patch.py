import pytest
from pathlib import Path
from rollback.core import RollbackSession
from rollback.patch import apply_patch, export_patch


def test_export_and_apply_patch_roundtrip(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f1 = tmp_path / "hello.py"
    f1.write_text("print('hello world')\n")
    cp1 = session.checkpoint("initial")

    f1.write_text("print('hello AI agents')\nprint('welcome to rollback')\n")
    f2 = tmp_path / "new_file.txt"
    f2.write_text("extra content\n")
    cp2 = session.checkpoint("updated")

    patch_text = export_patch(session, cp1.id, cp2.id)
    assert "diff --git" in patch_text
    assert "hello AI agents" in patch_text

    # Revert to cp1
    session.revert(cp1.id)
    assert f1.read_text() == "print('hello world')\n"
    assert not f2.exists()

    # Dry-run apply
    dry_res = apply_patch(session, patch_text, dry_run=True)
    assert dry_res.status == "dry_run_success"
    assert "hello.py" in dry_res.files_affected
    # Verify disk was NOT modified in dry-run
    assert f1.read_text() == "print('hello world')\n"

    # Actual apply
    apply_res = apply_patch(session, patch_text, dry_run=False)
    assert apply_res.status == "applied"
    assert "hello.py" in apply_res.files_affected
    assert "hello AI agents" in f1.read_text()
    assert apply_res.pre_checkpoint is not None
    assert apply_res.post_checkpoint is not None


def test_export_patch_to_file(tmp_path: Path):
    session = RollbackSession(root_dir=str(tmp_path))

    f = tmp_path / "code.py"
    f.write_text("a = 1\n")
    cp1 = session.checkpoint("v1")

    f.write_text("a = 2\n")
    cp2 = session.checkpoint("v2")

    out_patch = tmp_path / "patch.diff"
    res = export_patch(session, cp1.id, cp2.id, output_file=out_patch)
    assert out_patch.exists()
    assert out_patch.read_text() == res
