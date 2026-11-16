import tempfile
from pathlib import Path
import pytest

from rollback.core import RollbackSession
from rollback.sandbox import SpeculativeSandbox


def test_sandbox_stage_read_and_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        disk_file = Path(tmpdir) / "existing.py"
        disk_file.write_text("x = 10\n")
        session.checkpoint("base")

        sandbox = session.speculative_sandbox()

        # Reading unchanged file falls through to disk
        assert sandbox.read_file("existing.py") == "x = 10\n"
        assert sandbox.exists("existing.py") is True

        # Stage modification in memory
        sandbox.write_file("existing.py", "x = 20\n")
        assert sandbox.read_file("existing.py") == "x = 20\n"
        # Physical disk MUST still have old content
        assert disk_file.read_text() == "x = 10\n"

        # Stage brand new file
        sandbox.write_file("new_module.py", "def run(): return True\n")
        assert sandbox.exists("new_module.py") is True
        assert (Path(tmpdir) / "new_module.py").exists() is False

        # Stage deletion
        sandbox.delete_file("existing.py")
        assert sandbox.exists("existing.py") is False
        with pytest.raises(FileNotFoundError):
            sandbox.read_file("existing.py")


def test_sandbox_integrity_validation():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        sandbox = session.speculative_sandbox()

        # Stage broken Python AST
        sandbox.write_file("broken.py", "def foo(\n")
        rep = sandbox.validate_integrity()
        assert rep.is_clean is False
        assert rep.failed_count == 1

        # Fix AST
        sandbox.write_file("broken.py", "def foo(): pass\n")
        rep2 = sandbox.validate_integrity()
        assert rep2.is_clean is True
        assert rep2.failed_count == 0


def test_sandbox_commit():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        f_old = Path(tmpdir) / "old.txt"
        f_old.write_text("deprecated\n")
        session.checkpoint("v1")

        sandbox = session.speculative_sandbox()
        sandbox.write_file("src/app.py", "print('hello from sandbox')\n")
        sandbox.delete_file("old.txt")

        commit_res = sandbox.commit(create_checkpoint=True, message="Applied sandbox refactor")

        assert "src/app.py" in commit_res.written_files
        assert "old.txt" in commit_res.deleted_files
        assert commit_res.checkpoint_id is not None

        # Verify on physical disk
        assert (Path(tmpdir) / "src/app.py").read_text() == "print('hello from sandbox')\n"
        assert not f_old.exists()


def test_sandbox_context_manager_discard_on_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)

        try:
            with session.speculative_sandbox() as sb:
                sb.write_file("temp.py", "print(1)")
                raise RuntimeError("Intentional error during staging")
        except RuntimeError:
            pass

        # Nothing should have hit disk
        assert not (Path(tmpdir) / "temp.py").exists()
