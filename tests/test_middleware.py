import shutil
import tempfile
import pytest
from pathlib import Path
from rollback.core import create_session
from rollback.diff import compute_diff
from rollback.middleware import transactional_step, transactional_tool


@pytest.fixture
def workspace():
    temp_dir = tempfile.mkdtemp(prefix="rb_test_mw_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_transactional_tool_decorator_success(workspace):
    session = create_session(str(workspace))
    target = workspace / "out.txt"

    @transactional_tool(session=session)
    def agent_writer(text: str):
        target.write_text(text)
        return "OK"

    res = agent_writer("success text")
    assert res == "OK"
    assert target.read_text() == "success text"


def test_transactional_tool_decorator_rollback_on_failure(workspace):
    session = create_session(str(workspace))
    target = workspace / "app.py"
    target.write_text("# original code\n")

    @transactional_tool(session=session, auto_rollback_on_error=True)
    def bad_refactor():
        target.write_text("# broken syntax error !!!")
        raise RuntimeError("Syntax evaluation failed")

    with pytest.raises(RuntimeError):
        bad_refactor()

    # Must be restored to original code
    assert target.read_text() == "# original code\n"


def test_transactional_step_context_manager(workspace):
    session = create_session(str(workspace))
    f = workspace / "log.txt"
    f.write_text("v1\n")

    try:
        with transactional_step(session=session, label="dangerous_step"):
            f.write_text("v2 rogue\n")
            raise ValueError("Safety invariant violated")
    except ValueError:
        pass

    assert f.read_text() == "v1\n"


def test_compute_diff(workspace):
    session = create_session(str(workspace))
    f1 = workspace / "file.txt"
    f1.write_text("line 1\nline 2\n")

    cp1 = session.checkpoint("base")

    # Add 1 line and delete 1 line
    f1.write_text("line 1\nline 3 modified\n")
    # Add new file
    f2 = workspace / "added.txt"
    f2.write_text("new content\n")

    diff = compute_diff(session, cp1)
    assert diff.total_files_changed == 2

    d_map = {f.path: f for f in diff.files}
    assert "file.txt" in d_map
    assert d_map["file.txt"].change_type == "modified"
    assert "added.txt" in d_map
    assert d_map["added.txt"].change_type == "added"
