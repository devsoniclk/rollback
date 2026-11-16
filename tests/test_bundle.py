import pytest
from pathlib import Path
from rollback.core import RollbackSession


def test_bundle_export_and_import(tmp_path: Path):
    ws1 = tmp_path / "workspace1"
    ws1.mkdir()
    session1 = RollbackSession(root_dir=str(ws1))

    f1 = ws1 / "file1.txt"
    f1.write_text("Hello Rollback Bundle")
    cp1 = session1.checkpoint("step1")
    session1.tag(cp1.id, "stable")

    f1.write_text("Modified in step 2")
    f2 = ws1 / "file2.txt"
    f2.write_text("Second file")
    session1.checkpoint("step2")

    # Export bundle
    bundle_path = tmp_path / "session_bundle.rbk.tar.gz"
    out = session1.export_bundle(bundle_path)
    assert out.exists()
    assert out.stat().st_size > 0

    # Import bundle into fresh workspace2
    ws2 = tmp_path / "workspace2"
    ws2.mkdir()
    session2 = RollbackSession.import_bundle(bundle_path, target_root=str(ws2))

    assert len(session2.history) == 2
    assert "stable" in session2.tags
    assert session2.tags["stable"] == cp1.id

    # Restore stable state in workspace2
    session2.revert("stable")
    assert (ws2 / "file1.txt").read_text() == "Hello Rollback Bundle"
    assert not (ws2 / "file2.txt").exists()
