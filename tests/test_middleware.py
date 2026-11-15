import asyncio
import shutil
import tempfile
import pytest
from pathlib import Path
from rollback.core import create_session
from rollback.diff import compute_diff
from rollback.middleware import async_transactional_step, transactional_step, transactional_tool


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


def test_async_transactional_tool_decorator(workspace):
    session = create_session(str(workspace))
    target = workspace / "async_app.py"
    target.write_text("# initial async code\n")

    @transactional_tool(session=session, auto_rollback_on_error=True)
    async def async_agent_tool(fail: bool):
        await asyncio.sleep(0.01)
        target.write_text("# mutated by async agent\n")
        if fail:
            raise ValueError("Async tool execution failed")
        return "ASYNC_SUCCESS"

    # Test success run
    res = asyncio.run(async_agent_tool(fail=False))
    assert res == "ASYNC_SUCCESS"
    assert target.read_text() == "# mutated by async agent\n"

    # Test failure with automatic rollback
    with pytest.raises(ValueError):
        asyncio.run(async_agent_tool(fail=True))

    assert target.read_text() == "# mutated by async agent\n"


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


def test_async_transactional_step_context_manager(workspace):
    session = create_session(str(workspace))
    f = workspace / "async_log.txt"
    f.write_text("initial async state\n")

    async def run_step():
        async with async_transactional_step(session=session, label="async_risky"):
            await asyncio.sleep(0.01)
            f.write_text("dangerous overwrite\n")
            raise KeyError("Async check failed")

    with pytest.raises(KeyKeyError if "KeyKeyError" in dir() else KeyError):
        asyncio.run(run_step())

    assert f.read_text() == "initial async state\n"


def test_compute_diff_between_checkpoints(workspace):
    session = create_session(str(workspace))
    f1 = workspace / "file.txt"
    f1.write_text("line 1\nline 2\n")

    cp1 = session.checkpoint("base")
    session.tag(cp1.id, "v1.0")

    # Add 1 line and delete 1 line
    f1.write_text("line 1\nline 3 modified\n")
    # Add new file
    f2 = workspace / "added.txt"
    f2.write_text("new content\n")

    cp2 = session.checkpoint("v2_changes")

    # Diff between cp1 and cp2 using tag and ID
    diff = compute_diff(session, from_checkpoint="v1.0", to_checkpoint=cp2.id)
    assert diff.total_files_changed == 2

    d_map = {f.path: f for f in diff.files}
    assert "file.txt" in d_map
    assert d_map["file.txt"].change_type == "modified"
    assert "added.txt" in d_map
    assert d_map["added.txt"].change_type == "added"
