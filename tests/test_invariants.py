import sys
import tempfile
from pathlib import Path
import pytest
from rollback.core import RollbackSession
from rollback.invariants import (
    InvariantGate,
    InvariantReport,
    InvariantRule,
    InvariantType,
    InvariantViolation,
    InvariantViolationError,
)


def test_invariant_rule_serialization() -> None:
    rule = InvariantRule(
        name="no-secret",
        rule_type=InvariantType.REGEX_PROHIBIT,
        target_pattern=r"API_KEY\s*=\s*['\"][^'\"]+['\"]",
        severity="error",
        file_glob="*.py",
        description="Prevent hardcoded API keys",
    )
    d = rule.to_dict()
    assert d["name"] == "no-secret"
    assert d["rule_type"] == "regex_prohibit"

    deserialized = InvariantRule.from_dict(d)
    assert deserialized.name == rule.name
    assert deserialized.rule_type == InvariantType.REGEX_PROHIBIT
    assert deserialized.target_pattern == rule.target_pattern


def test_invariant_gate_rule_management() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        gate = InvariantGate(tmpdir)
        rule = InvariantRule(
            name="test-rule",
            rule_type=InvariantType.COMMAND,
            target_pattern=f'"{sys.executable}" -c "import sys; sys.exit(0)"',
        )
        gate.add_rule(rule)
        assert len(gate.list_rules()) == 1
        assert gate.get_rule("test-rule") == rule

        gate.save_rules()
        assert (Path(tmpdir) / ".rollback" / "invariants.json").exists()

        gate2 = InvariantGate(tmpdir)
        loaded = gate2.load_rules()
        assert len(loaded) == 1
        assert loaded[0].name == "test-rule"

        assert gate2.remove_rule("test-rule") is True
        assert len(gate2.list_rules()) == 0


def test_invariant_command_evaluation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        gate = InvariantGate(tmpdir)
        # Passing command
        gate.add_rule(
            InvariantRule(
                name="pass-cmd",
                rule_type=InvariantType.COMMAND,
                target_pattern=f'"{sys.executable}" -c "import sys; sys.exit(0)"',
            )
        )
        rep = gate.evaluate(strict=False)
        assert rep.passed is True
        assert rep.passed_rules == 1
        assert rep.failed_rules == 0

        # Failing command
        gate.add_rule(
            InvariantRule(
                name="fail-cmd",
                rule_type=InvariantType.COMMAND,
                target_pattern=f'"{sys.executable}" -c "import sys; sys.exit(2)"',
            )
        )
        rep2 = gate.evaluate(strict=False)
        assert rep2.passed is False
        assert rep2.passed_rules == 1
        assert rep2.failed_rules == 1
        assert len(rep2.violations) == 1
        assert rep2.violations[0].rule_name == "fail-cmd"

        with pytest.raises(InvariantViolationError):
            gate.evaluate(strict=True)


def test_invariant_regex_rules() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        gate = InvariantGate(tmpdir)
        py_file = Path(tmpdir) / "app.py"
        py_file.write_text("SECRET_KEY = 'super_secret'\n")

        # Prohibited pattern violated
        gate.add_rule(
            InvariantRule(
                name="no-secret",
                rule_type=InvariantType.REGEX_PROHIBIT,
                target_pattern=r"SECRET_KEY",
                file_glob="*.py",
            )
        )
        rep = gate.evaluate(strict=False)
        assert rep.passed is False
        assert any(v.rule_name == "no-secret" for v in rep.violations)

        # Required pattern
        gate.add_rule(
            InvariantRule(
                name="require-license",
                rule_type=InvariantType.REGEX_REQUIRE,
                target_pattern=r"SPDX-License-Identifier",
                file_glob="*.py",
            )
        )
        rep2 = gate.evaluate(strict=False)
        assert rep2.passed is False
        assert any(v.rule_name == "require-license" for v in rep2.violations)

        # Satisfy required pattern and remove prohibited
        py_file.write_text("# SPDX-License-Identifier: Apache-2.0\nSAFE_CONFIG = 1\n")
        rep3 = gate.evaluate(strict=False)
        assert rep3.passed is True


def test_invariant_file_size_rules() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        gate = InvariantGate(tmpdir)
        doc = Path(tmpdir) / "data.bin"
        doc.write_bytes(b"x" * 500)

        gate.add_rule(
            InvariantRule(
                name="max-size",
                rule_type=InvariantType.FILE_SIZE_MAX,
                target_pattern="",
                file_glob="*.bin",
                threshold_value=300,
            )
        )
        gate.add_rule(
            InvariantRule(
                name="min-size",
                rule_type=InvariantType.FILE_SIZE_MIN,
                target_pattern="",
                file_glob="*.bin",
                threshold_value=1000,
            )
        )
        rep = gate.evaluate(strict=False)
        assert rep.passed is False
        assert rep.failed_rules == 2


def test_session_invariant_gate_integration() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        session.create_checkpoint("init")
        assert session.invariant_gate is not None
        rep = session.assert_invariants(strict=False)
        assert rep.passed is True
        assert rep.total_rules == 0
