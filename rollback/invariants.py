"""
rollback.invariants: Pre- and post-action invariant assertion engine for AI coding agents.

Enforces critical workspace integrity rules (tests, syntax compilation, file size boundaries,
and prohibited/required patterns) before and after autonomous agent tool executions.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set


class InvariantType(str, Enum):
    COMMAND = "command"
    REGEX_PROHIBIT = "regex_prohibit"
    REGEX_REQUIRE = "regex_require"
    FILE_SIZE_MIN = "file_size_min"
    FILE_SIZE_MAX = "file_size_max"


@dataclass
class InvariantRule:
    name: str
    rule_type: InvariantType
    target_pattern: str  # Shell command string, regex pattern, or file glob
    severity: str = "error"  # "error" or "warning"
    expected_exit_code: int = 0
    timeout_sec: float = 15.0
    file_glob: str = "*"  # For regex or file size checks
    threshold_value: int = 0  # For min/max size checks
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rule_type"] = self.rule_type.value if isinstance(self.rule_type, InvariantType) else self.rule_type
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InvariantRule:
        rule_type = data.get("rule_type", "command")
        if isinstance(rule_type, str):
            try:
                rule_type = InvariantType(rule_type)
            except ValueError:
                rule_type = InvariantType.COMMAND
        return cls(
            name=data["name"],
            rule_type=rule_type,
            target_pattern=data.get("target_pattern", ""),
            severity=data.get("severity", "error"),
            expected_exit_code=int(data.get("expected_exit_code", 0)),
            timeout_sec=float(data.get("timeout_sec", 15.0)),
            file_glob=data.get("file_glob", "*"),
            threshold_value=int(data.get("threshold_value", 0)),
            description=data.get("description", ""),
        )


@dataclass
class InvariantViolation:
    rule_name: str
    rule_type: str
    severity: str
    message: str
    file_path: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InvariantReport:
    passed: bool
    total_rules: int
    passed_rules: int
    failed_rules: int
    violations: List[InvariantViolation] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "total_rules": self.total_rules,
            "passed_rules": self.passed_rules,
            "failed_rules": self.failed_rules,
            "violations": [v.to_dict() for v in self.violations],
            "duration_ms": round(self.duration_ms, 2),
        }


class InvariantViolationError(Exception):
    """Raised when one or more strict invariant rules fail."""

    def __init__(self, message: str, report: InvariantReport):
        super().__init__(message)
        self.report = report


class InvariantGate:
    """
    Evaluates invariant assertion rules across the workspace.
    Guarantees that an agent's code modifications do not violate tests,
    syntax, or structural constraints.
    """

    IGNORE_DIRS = {".git", ".rollback", "__pycache__", ".pytest_cache", "node_modules", "venv", ".venv"}

    def __init__(self, root_dir: str | Path, config_file: Optional[str] = None):
        self.root_dir = Path(root_dir).resolve()
        self.config_path = Path(config_file) if config_file else self.root_dir / ".rollback" / "invariants.json"
        self._rules: Dict[str, InvariantRule] = {}
        if self.config_path.exists():
            self.load_rules()

    def add_rule(self, rule: InvariantRule) -> None:
        self._rules[rule.name] = rule

    def remove_rule(self, name: str) -> bool:
        if name in self._rules:
            del self._rules[name]
            return True
        return False

    def list_rules(self) -> List[InvariantRule]:
        return list(self._rules.values())

    def get_rule(self, name: str) -> Optional[InvariantRule]:
        return self._rules.get(name)

    def load_rules(self) -> List[InvariantRule]:
        if not self.config_path.exists():
            return []
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rules = [InvariantRule.from_dict(r) for r in data.get("rules", [])]
            self._rules = {r.name: r for r in rules}
            return rules
        except Exception:
            return []

    def save_rules(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"rules": [r.to_dict() for r in self._rules.values()]}
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _get_workspace_files(self, glob_pattern: str = "*") -> List[Path]:
        matches = []
        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
            for file in files:
                rel_path = Path(root, file).relative_to(self.root_dir)
                if fnmatch.fnmatch(str(rel_path), glob_pattern) or fnmatch.fnmatch(file, glob_pattern):
                    matches.append(Path(root, file))
        return matches

    def evaluate(self, strict: bool = False) -> InvariantReport:
        start_time = time.time()
        violations: List[InvariantViolation] = []
        passed_rules = 0

        for rule in self._rules.values():
            rule_violated = False

            if rule.rule_type == InvariantType.COMMAND:
                try:
                    res = subprocess.run(
                        rule.target_pattern,
                        shell=True,
                        cwd=str(self.root_dir),
                        capture_output=True,
                        text=True,
                        timeout=rule.timeout_sec,
                    )
                    if res.returncode != rule.expected_exit_code:
                        rule_violated = True
                        violations.append(
                            InvariantViolation(
                                rule_name=rule.name,
                                rule_type=rule.rule_type.value,
                                severity=rule.severity,
                                message=f"Command exited with {res.returncode} (expected {rule.expected_exit_code})",
                                stdout=res.stdout,
                                stderr=res.stderr,
                                exit_code=res.returncode,
                            )
                        )
                except subprocess.TimeoutExpired:
                    rule_violated = True
                    violations.append(
                        InvariantViolation(
                            rule_name=rule.name,
                            rule_type=rule.rule_type.value,
                            severity=rule.severity,
                            message=f"Command timed out after {rule.timeout_sec}s",
                        )
                    )
                except Exception as ex:
                    rule_violated = True
                    violations.append(
                        InvariantViolation(
                            rule_name=rule.name,
                            rule_type=rule.rule_type.value,
                            severity=rule.severity,
                            message=f"Command execution error: {ex}",
                        )
                    )

            elif rule.rule_type == InvariantType.REGEX_PROHIBIT:
                pattern = re.compile(rule.target_pattern)
                files = self._get_workspace_files(rule.file_glob)
                for file_path in files:
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        if pattern.search(content):
                            rule_violated = True
                            rel = str(file_path.relative_to(self.root_dir))
                            violations.append(
                                InvariantViolation(
                                    rule_name=rule.name,
                                    rule_type=rule.rule_type.value,
                                    severity=rule.severity,
                                    file_path=rel,
                                    message=f"File '{rel}' contains prohibited pattern: {rule.target_pattern}",
                                )
                            )
                    except Exception as ex:
                        rule_violated = True
                        violations.append(
                            InvariantViolation(
                                rule_name=rule.name,
                                rule_type=rule.rule_type.value,
                                severity=rule.severity,
                                file_path=str(file_path.relative_to(self.root_dir)),
                                message=f"Failed to read file: {ex}",
                            )
                        )

            elif rule.rule_type == InvariantType.REGEX_REQUIRE:
                pattern = re.compile(rule.target_pattern)
                files = self._get_workspace_files(rule.file_glob)
                if not files:
                    rule_violated = True
                    violations.append(
                        InvariantViolation(
                            rule_name=rule.name,
                            rule_type=rule.rule_type.value,
                            severity=rule.severity,
                            message=f"No files matching glob '{rule.file_glob}' found to satisfy required pattern",
                        )
                    )
                for file_path in files:
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        if not pattern.search(content):
                            rule_violated = True
                            rel = str(file_path.relative_to(self.root_dir))
                            violations.append(
                                InvariantViolation(
                                    rule_name=rule.name,
                                    rule_type=rule.rule_type.value,
                                    severity=rule.severity,
                                    file_path=rel,
                                    message=f"File '{rel}' is missing required pattern: {rule.target_pattern}",
                                )
                            )
                    except Exception as ex:
                        rule_violated = True
                        violations.append(
                            InvariantViolation(
                                rule_name=rule.name,
                                rule_type=rule.rule_type.value,
                                severity=rule.severity,
                                file_path=str(file_path.relative_to(self.root_dir)),
                                message=f"Failed to read file: {ex}",
                            )
                        )

            elif rule.rule_type == InvariantType.FILE_SIZE_MIN:
                files = self._get_workspace_files(rule.file_glob)
                for file_path in files:
                    size = file_path.stat().st_size
                    if size < rule.threshold_value:
                        rule_violated = True
                        rel = str(file_path.relative_to(self.root_dir))
                        violations.append(
                            InvariantViolation(
                                rule_name=rule.name,
                                rule_type=rule.rule_type.value,
                                severity=rule.severity,
                                file_path=rel,
                                message=f"File '{rel}' size {size} bytes is below minimum threshold {rule.threshold_value} bytes",
                            )
                        )

            elif rule.rule_type == InvariantType.FILE_SIZE_MAX:
                files = self._get_workspace_files(rule.file_glob)
                for file_path in files:
                    size = file_path.stat().st_size
                    if size > rule.threshold_value:
                        rule_violated = True
                        rel = str(file_path.relative_to(self.root_dir))
                        violations.append(
                            InvariantViolation(
                                rule_name=rule.name,
                                rule_type=rule.rule_type.value,
                                severity=rule.severity,
                                file_path=rel,
                                message=f"File '{rel}' size {size} bytes exceeds maximum threshold {rule.threshold_value} bytes",
                            )
                        )

            if not rule_violated:
                passed_rules += 1

        duration_ms = (time.time() - start_time) * 1000.0
        error_violations = [v for v in violations if v.severity == "error"]
        passed = len(error_violations) == 0

        report = InvariantReport(
            passed=passed,
            total_rules=len(self._rules),
            passed_rules=passed_rules,
            failed_rules=len(self._rules) - passed_rules,
            violations=violations,
            duration_ms=duration_ms,
        )

        if strict and not passed:
            raise InvariantViolationError(
                f"Workspace failed {len(error_violations)} invariant checks",
                report=report,
            )

        return report
