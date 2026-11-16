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

    # 2. Snapshot with tag
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "snapshot", "init_snap", "--tag", "stable_v1", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "Created checkpoint:" in res.stdout
    assert "stable_v1" in res.stdout

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

    # 4. Revert dry-run & Plan
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "revert", "stable_v1", "--dry-run", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "Dry-run plan for stable_v1" in res.stdout
    assert "[WILL RESTORE]   test.txt" in res.stdout
    # Ensure dry run did not revert file yet
    assert f.read_text() == "v2 rogue\n"

    res_plan = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "plan", "stable_v1", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_plan.returncode == 0
    assert "Revert Plan to" in res_plan.stdout
    assert "[RESTORE]   test.txt" in res_plan.stdout

    # 5. Revert using tag
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "revert", "stable_v1", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "[RESTORED] test.txt" in res.stdout
    assert f.read_text() == "v1\n"

    # 6. Branching CLI
    res_bcreate = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "branch", "create", "feature_x", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_bcreate.returncode == 0
    assert "Created branch 'feature_x'" in res_bcreate.stdout

    res_blist = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "branch", "list", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_blist.returncode == 0
    assert "feature_x" in res_blist.stdout

    # 7. Bundle Export & Import CLI
    bundle_file = workspace / "bundle.rbk.tar.gz"
    res_bexp = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "bundle", "export", str(bundle_file), "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_bexp.returncode == 0
    assert "Exported session bundle to" in res_bexp.stdout
    assert bundle_file.exists()

    ws_import = workspace / "imported_ws"
    res_bimp = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "bundle", "import", str(bundle_file), "--root", str(ws_import)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_bimp.returncode == 0
    assert "Imported session bundle into" in res_bimp.stdout

    # 8. Clean / GC
    res = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "clean", "--keep", "1", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res.returncode == 0
    assert "Cleaned journal:" in res.stdout

    # 9. Report / HTML Dossier CLI
    rep_html = workspace / "dossier.html"
    res_rep = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "report", "--html", str(rep_html), "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_rep.returncode == 0
    assert "Generated visual session dossier at" in res_rep.stdout
    assert rep_html.exists()

    # 10. Replay CLI
    res_rpl = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "replay", "HEAD", "--step", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_rpl.returncode == 0
    assert "Replayed" in res_rpl.stdout

    # 11. Patch CLI
    patch_out = workspace / "test.patch"
    res_patch = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "patch", "HEAD", "-o", str(patch_out), "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_patch.returncode == 0
    assert "Exported patch to" in res_patch.stdout

    # 12. Git Sync CLI
    res_git = subprocess.run(
        [sys.executable, "-m", "rollback.cli", "git-sync", "status", "--root", str(workspace)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repo_root)},
    )
    assert res_git.returncode == 0
    assert "Git Bridge Status:" in res_git.stdout
