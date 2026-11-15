import os
import shutil
import tempfile
import pytest
from pathlib import Path
from rollback.core import RollbackSession, create_session


@pytest.fixture
def workspace():
    temp_dir = tempfile.mkdtemp(prefix="rb_test_ws_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_session_init(workspace):
    session = create_session(str(workspace))
    assert session.journal_dir.exists()
    assert session.blobs_dir.exists()
    assert session.checkpoints_dir.exists()
    assert len(session.history) == 0


def test_checkpoint_and_revert_modified_file(workspace):
    test_file = workspace / "main.py"
    test_file.write_text("print('hello v1')\n")

    session = create_session(str(workspace))
    cp1 = session.checkpoint("initial")

    assert len(cp1.files) == 1
    assert "main.py" in cp1.files
    assert cp1.files["main.py"].exists

    # Modify file
    test_file.write_text("print('corrupted v2')\n")
    assert test_file.read_text() == "print('corrupted v2')\n"

    # Revert to cp1
    actions = session.revert(cp1.id)
    assert actions.get("main.py") == "restored"
    assert test_file.read_text() == "print('hello v1')\n"


def test_checkpoint_and_revert_created_file(workspace):
    session = create_session(str(workspace))
    cp1 = session.checkpoint("empty_start")

    new_file = workspace / "agent_created.txt"
    new_file.write_text("rogue output\n")
    assert new_file.exists()

    # Revert
    actions = session.revert(cp1.id)
    assert actions.get("agent_created.txt") == "removed"
    assert not new_file.exists()


def test_checkpoint_and_revert_deleted_file(workspace):
    victim = workspace / "critical.py"
    victim.write_text("def safe(): pass\n")

    session = create_session(str(workspace))
    cp1 = session.checkpoint("before_delete")

    victim.unlink()
    assert not victim.exists()

    # Revert resurrects the file
    actions = session.revert(cp1.id)
    assert actions.get("critical.py") == "restored"
    assert victim.exists()
    assert victim.read_text() == "def safe(): pass\n"


def test_multi_step_rollback(workspace):
    f = workspace / "counter.txt"
    f.write_text("0")

    session = create_session(str(workspace))
    cp0 = session.checkpoint("step0")

    f.write_text("1")
    cp1 = session.checkpoint("step1")

    f.write_text("2")
    cp2 = session.checkpoint("step2")

    # Step back 1 step (to cp1)
    session.rollback_step(1)
    assert f.read_text() == "1"

    # Step back 1 more step (to cp0)
    session.rollback_step(1)
    assert f.read_text() == "0"


def test_binary_file_handling(workspace):
    bin_file = workspace / "data.bin"
    sample_bytes = b"\x00\xff\xfe\x12\x34\x56\x78"
    bin_file.write_bytes(sample_bytes)

    session = create_session(str(workspace))
    cp = session.checkpoint("bin_snapshot")

    bin_file.write_bytes(b"\xde\xad\xbe\xef")
    assert bin_file.read_bytes() == b"\xde\xad\xbe\xef"

    session.revert(cp.id)
    assert bin_file.read_bytes() == sample_bytes
