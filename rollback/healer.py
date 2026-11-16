"""
Auto-Healer and Automated Rollback Watchdog for autonomous AI agent execution loops.

Monitors command execution (test suites, build scripts, linter gates, and CLI actions),
classifies diagnostic failure patterns (SyntaxError, AssertionError, ModuleNotFoundError,
TypeError, Timeout), captures broken workspace diffs into forensic incident quarantine bundles,
and automatically reverts workspace state to the pre-execution checkpoint with actionable
refinement guidance for LLM self-correction.
"""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


class FailureClassification:
    SYNTAX_ERROR = "SYNTAX_ERROR"
    ASSERTION_ERROR = "ASSERTION_ERROR"
    IMPORT_ERROR = "IMPORT_ERROR"
    TYPE_ERROR = "TYPE_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    RUNTIME_CRASH = "RUNTIME_CRASH"
    UNKNOWN = "UNKNOWN"


class DiagnosticParser:
    """Zero-dependency diagnostic analyzer for test suites, tracebacks, and compiler errors."""

    @staticmethod
    def classify(stdout: str, stderr: str) -> Tuple[str, str]:
        """Classify failure mode into a standardized enum and extract a single-line summary."""
        combined = f"{stdout}\n{stderr}"

        # 1. Syntax / Indentation Errors
        if "SyntaxError:" in combined or "IndentationError:" in combined:
            m = re.search(r"((?:SyntaxError|IndentationError):[^\n]+)", combined)
            return (
                FailureClassification.SYNTAX_ERROR,
                m.group(1) if m else "Syntax or Indentation error detected",
            )

        # 2. Import / Missing Module Errors
        if "ModuleNotFoundError:" in combined or "ImportError:" in combined:
            m = re.search(r"((?:ModuleNotFoundError|ImportError):[^\n]+)", combined)
            return (
                FailureClassification.IMPORT_ERROR,
                m.group(1) if m else "Unresolved import or missing dependency",
            )

        # 3. Test Assertion / Test Runner Failures
        if "AssertionError" in combined or "FAILED " in combined or "assert " in combined:
            m = re.search(r"(FAILED\s+[^\n]+)", combined)
            if not m:
                m = re.search(r"(AssertionError:[^\n]+)", combined)
            return (
                FailureClassification.ASSERTION_ERROR,
                m.group(1) if m else "Test suite assertion failure",
            )

        # 4. Type / Attribute Errors
        if "TypeError:" in combined or "AttributeError:" in combined:
            m = re.search(r"((?:TypeError|AttributeError):[^\n]+)", combined)
            return (
                FailureClassification.TYPE_ERROR,
                m.group(1) if m else "Type or attribute access violation",
            )

        # 5. Timeouts
        if "timed out" in combined.lower() or "timeoutexpired" in combined.lower():
            return (
                FailureClassification.TIMEOUT_ERROR,
                "Command execution timed out",
            )

        # 6. Permissions
        if "Permission denied" in combined or "PermissionError:" in combined:
            return (
                FailureClassification.PERMISSION_ERROR,
                "Filesystem or execution permission denied",
            )

        # 7. Generic Traceback / Unhandled Exception
        if "Traceback (most recent call last):" in combined:
            lines = [line.strip() for line in combined.splitlines() if line.strip()]
            last_line = lines[-1] if lines else "Unhandled runtime exception"
            return FailureClassification.RUNTIME_CRASH, last_line

        return FailureClassification.UNKNOWN, "Command returned non-zero exit status"

    @staticmethod
    def extract_failing_files(stdout: str, stderr: str) -> List[str]:
        """Extract paths of problematic or failing files mentioned in the logs."""
        combined = f"{stdout}\n{stderr}"
        files = set()
        for m in re.finditer(r'File "([^"]+)", line \d+', combined):
            files.add(m.group(1))
        for m in re.finditer(r"FAILED\s+([^\s:]+)", combined):
            files.add(m.group(1))
        return sorted(list(files))


class HealingIncidentReport:
    """Structured report documenting an auto-heal watchdog incident."""

    def __init__(
        self,
        incident_id: str,
        command: str,
        exit_code: int,
        success: bool,
        reverted: bool,
        pre_checkpoint_id: str,
        failure_type: str,
        summary: str,
        failing_files: List[str],
        stdout: str,
        stderr: str,
        duration_ms: float,
        quarantine_path: Optional[str] = None,
        prompt_guidance: Optional[str] = None,
    ):
        self.incident_id = incident_id
        self.command = command
        self.exit_code = exit_code
        self.success = success
        self.reverted = reverted
        self.pre_checkpoint_id = pre_checkpoint_id
        self.failure_type = failure_type
        self.summary = summary
        self.failing_files = failing_files
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.quarantine_path = quarantine_path
        self.prompt_guidance = prompt_guidance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "command": self.command,
            "exit_code": self.exit_code,
            "success": self.success,
            "reverted": self.reverted,
            "pre_checkpoint_id": self.pre_checkpoint_id,
            "failure_type": self.failure_type,
            "summary": self.summary,
            "failing_files": self.failing_files,
            "stdout": self.stdout[-2000:] if self.stdout else "",
            "stderr": self.stderr[-2000:] if self.stderr else "",
            "duration_ms": round(self.duration_ms, 2),
            "quarantine_path": self.quarantine_path,
            "prompt_guidance": self.prompt_guidance,
        }

    def __repr__(self) -> str:
        status = "PASSED" if self.success else f"REVERTED ({self.failure_type})"
        return f"<HealingIncidentReport {self.incident_id} [{status}] command='{self.command}'>"


class AutoHealer:
    """Watchdog that executes commands, catches breakages, and restores green checkpoints."""

    def __init__(self, session: Any):
        self.session = session
        self.incidents_dir = self.session.rollback_dir / "incidents"
        self.incidents_dir.mkdir(parents=True, exist_ok=True)

    def run_guarded(
        self,
        command: Union[str, List[str]],
        cwd: Optional[Union[str, Path]] = None,
        auto_revert: bool = True,
        timeout_seconds: float = 120.0,
        env: Optional[Dict[str, str]] = None,
        checkpoint_message: Optional[str] = None,
        baseline_checkpoint_id: Optional[str] = None,
    ) -> HealingIncidentReport:
        """
        Execute command with pre-savepoint guard. If command fails, automatically revert
        workspace to the pre-savepoint state and generate structured refinement guidance.
        """
        cmd_str = command if isinstance(command, str) else " ".join(command)
        if baseline_checkpoint_id:
            restore_target_id = self.session.resolve_checkpoint_id(baseline_checkpoint_id)
        else:
            msg = checkpoint_message or f"Auto-heal guard before: {cmd_str[:60]}"
            pre_cp = self.session.checkpoint(msg)
            restore_target_id = pre_cp.id

        work_dir = Path(cwd) if cwd else self.session.workspace_dir
        incident_id = f"inc_{uuid.uuid4().hex[:10]}"

        start_time = time.perf_counter()
        exec_env = os.environ.copy()
        if env:
            exec_env.update(env)

        stdout, stderr, exit_code = "", "", 0
        timed_out = False

        try:
            proc = subprocess.run(
                command,
                cwd=str(work_dir),
                shell=isinstance(command, str),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                env=exec_env,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            stderr += f"\nCommand execution timed out after {timeout_seconds}s"
            exit_code = 124
        except Exception as exc:
            stderr = f"Watchdog process execution error: {exc}"
            exit_code = 1

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if exit_code == 0:
            return HealingIncidentReport(
                incident_id=incident_id,
                command=cmd_str,
                exit_code=0,
                success=True,
                reverted=False,
                pre_checkpoint_id=restore_target_id,
                failure_type="NONE",
                summary="Command executed successfully without regressions.",
                failing_files=[],
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
            )

        # Failure path
        failure_type, summary = DiagnosticParser.classify(stdout, stderr)
        if timed_out:
            failure_type = FailureClassification.TIMEOUT_ERROR
            summary = f"Command exceeded timeout limit of {timeout_seconds} seconds"

        failing_files = DiagnosticParser.extract_failing_files(stdout, stderr)

        quarantine_file = None
        reverted = False

        # Quarantine failure details before revert
        incident_data = {
            "incident_id": incident_id,
            "timestamp": time.time(),
            "command": cmd_str,
            "exit_code": exit_code,
            "pre_checkpoint_id": restore_target_id,
            "failure_type": failure_type,
            "summary": summary,
            "failing_files": failing_files,
            "stdout": stdout,
            "stderr": stderr,
        }
        q_path = self.incidents_dir / f"{incident_id}.json"
        with open(q_path, "w", encoding="utf-8") as f:
            json.dump(incident_data, f, indent=2)
        quarantine_file = str(q_path)

        if auto_revert:
            self.session.revert(restore_target_id)
            reverted = True

        prompt_guidance = (
            f"[AUTO-HEAL TRIGGERED: WORKSPACE REVERTED TO GREEN STATE]\n"
            f"Command: `{cmd_str}`\n"
            f"Failure Classification: {failure_type}\n"
            f"Diagnosis: {summary}\n"
            f"Failing Targets: {', '.join(failing_files) if failing_files else 'N/A'}\n"
            f"Safe Checkpoint Restored: {restore_target_id}\n"
            f"Action Required: Your changes introduced {failure_type}. The workspace has been cleanly restored to savepoint {restore_target_id} to avoid corrupted disk state. Review the diagnosis above and adjust your code before re-executing."
        )

        return HealingIncidentReport(
            incident_id=incident_id,
            command=cmd_str,
            exit_code=exit_code,
            success=False,
            reverted=reverted,
            pre_checkpoint_id=restore_target_id,
            failure_type=failure_type,
            summary=summary,
            failing_files=failing_files,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            quarantine_path=quarantine_file,
            prompt_guidance=prompt_guidance,
        )
