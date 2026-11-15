import shutil
import subprocess
import sys
import tempfile
import pytest
from pathlib import Path


@pytest.fixture
def workspace():
    temp_dir = tempfile.mkdtemp(prefix="rb_test_cli_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_cli_lifecycle(workspace):
    repo_root = Path(__file__).parent.parent
    f = workspace / "test.txt"
    f.write_text("v1\n")

    # 1. Init
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "init", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "Initialized rollback journal" in res.stdout

    # 2. Snapshot
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "snapshot", "init_snap", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "Created checkpoint:" in res.stdout

    # 3. Modify & Diff
    f.write_text("v2 rogue\n")
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "diff", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "Diff:" in res.stdout
    assert "[MODIFIED] test.txt" in res.stdout

    # 4. Revert
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "revert", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "[RESTORED] test.txt" in res.stdout
    assert f.read_text() == "v1\n"
