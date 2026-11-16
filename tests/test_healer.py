import os
import sys
import tempfile
from pathlib import Path
import pytest

from rollback.core import RollbackSession
from rollback.healer import AutoHealer, DiagnosticParser, FailureClassification, HealingIncidentReport


def test_diagnostic_parser_classification():
    syntax_err = 'File "app.py", line 12\n  def bad(\n          ^\nSyntaxError: unexpected EOF while parsing'
    f_type, summary = DiagnosticParser.classify("", syntax_err)
    assert f_type == FailureClassification.SYNTAX_ERROR
    assert "SyntaxError:" in summary

    mod_err = 'Traceback (most recent call last):\n  File "main.py", line 1\nModuleNotFoundError: No module named \'hyperliquid_sdk\''
    f_type, summary = DiagnosticParser.classify("", mod_err)
    assert f_type == FailureClassification.IMPORT_ERROR
    assert "ModuleNotFoundError:" in summary

    assert_err = 'tests/test_core.py:42: in test_balance\n>   assert balance == 100\nE   AssertionError: assert 0 == 100\nFAILED tests/test_core.py::test_balance - AssertionError: assert 0 == 100'
    f_type, summary = DiagnosticParser.classify(assert_err, "")
    assert f_type == FailureClassification.ASSERTION_ERROR
    assert "FAILED" in summary or "AssertionError" in summary

    timeout_err = "Command execution timed out after 30s"
    f_type, summary = DiagnosticParser.classify("", timeout_err)
    assert f_type == FailureClassification.TIMEOUT_ERROR

    files = DiagnosticParser.extract_failing_files("", 'File "src/foo.py", line 10\nFAILED tests/test_bar.py::test_case')
    assert "src/foo.py" in files
    assert "tests/test_bar.py" in files


def test_auto_healer_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        test_file = Path(tmpdir) / "app.py"
        test_file.write_text("print('All systems operational')\n")
        session.checkpoint("Initial state")

        healer = AutoHealer(session)
        rep = healer.run_guarded(
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
            cwd=tmpdir,
        )

        assert rep.success is True
        assert rep.reverted is False
        assert rep.exit_code == 0
        assert rep.failure_type == "NONE"


def test_auto_healer_reverts_on_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        test_file = Path(tmpdir) / "service.py"
        test_file.write_text("VALID_VAR = 42\n")
        cp0 = session.checkpoint("Clean state")

        # Broken modification by agent
        test_file.write_text("SYNTAX ERROR IN FILE (\n")

        # Watchdog runs broken script
        healer = AutoHealer(session)
        rep = healer.run_guarded(
            command=[sys.executable, "service.py"],
            cwd=tmpdir,
            auto_revert=True,
            baseline_checkpoint_id=cp0.id,
        )

        assert rep.success is False
        assert rep.reverted is True
        assert rep.exit_code != 0
        assert rep.failure_type == FailureClassification.SYNTAX_ERROR
        assert rep.prompt_guidance is not None
        assert "AUTO-HEAL TRIGGERED" in rep.prompt_guidance
        assert rep.quarantine_path is not None
        assert os.path.exists(rep.quarantine_path)

        # Workspace MUST have been cleanly restored to VALID_VAR = 42
        restored_content = test_file.read_text()
        assert restored_content == "VALID_VAR = 42\n"


def test_session_heal_integration():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        f = Path(tmpdir) / "code.py"
        f.write_text("X = 1\n")
        cp = session.checkpoint("base")

        f.write_text("import non_existent_phantom_module_12345\n")

        rep = session.heal(
            command=[sys.executable, "code.py"],
            cwd=tmpdir,
            auto_revert=True,
            baseline_checkpoint_id=cp.id,
        )

        assert rep.success is False
        assert rep.reverted is True
        assert rep.failure_type == FailureClassification.IMPORT_ERROR
        assert f.read_text() == "X = 1\n"
