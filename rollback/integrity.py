from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union


class IntegrityError(Exception):
    """Raised when an integrity check fails in strict mode."""
    pass


@dataclass
class FileIntegrityResult:
    path: str
    valid: bool
    language: str
    error_message: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    snippet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "valid": self.valid,
            "language": self.language,
            "error_message": self.error_message,
            "line": self.line,
            "column": self.column,
            "snippet": self.snippet,
        }


@dataclass
class IntegrityReport:
    files_checked: int
    passed_count: int
    failed_count: int
    is_clean: bool
    results: List[FileIntegrityResult] = field(default_factory=list)

    @property
    def failures(self) -> List[FileIntegrityResult]:
        return [r for r in self.results if not r.valid]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_checked": self.files_checked,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "is_clean": self.is_clean,
            "failures": [f.to_dict() for f in self.failures],
            "results": [r.to_dict() for r in self.results],
        }


class IntegrityGate:
    """
    Autonomous code syntax and structural integrity gate.
    Validates code files before or after agent modifications to prevent corrupt,
    unparseable, or syntax-broken code from poisoning checkpoints.
    """

    EXTENSIONS = {
        ".py": "python",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".md": "markdown",
    }

    @classmethod
    def detect_language(cls, filename: str) -> Optional[str]:
        ext = Path(filename).suffix.lower()
        return cls.EXTENSIONS.get(ext)

    @classmethod
    def check_python(cls, content: str, filename: str = "<string>") -> FileIntegrityResult:
        try:
            ast.parse(content, filename=filename)
            return FileIntegrityResult(path=filename, valid=True, language="python")
        except SyntaxError as e:
            snippet = e.text.strip() if e.text else None
            return FileIntegrityResult(
                path=filename,
                valid=False,
                language="python",
                error_message=f"{e.msg} at line {e.lineno}",
                line=e.lineno,
                column=e.offset,
                snippet=snippet,
            )

    @classmethod
    def check_json(cls, content: str, filename: str = "<string>") -> FileIntegrityResult:
        try:
            json.loads(content)
            return FileIntegrityResult(path=filename, valid=True, language="json")
        except json.JSONDecodeError as e:
            lines = content.splitlines()
            snippet = lines[e.lineno - 1] if 0 < e.lineno <= len(lines) else None
            return FileIntegrityResult(
                path=filename,
                valid=False,
                language="json",
                error_message=f"{e.msg} at line {e.lineno}, column {e.colno}",
                line=e.lineno,
                column=e.colno,
                snippet=snippet,
            )

    @classmethod
    def check_yaml(cls, content: str, filename: str = "<string>") -> FileIntegrityResult:
        """
        Lightweight YAML structural checker:
        Checks for invalid tab indentation, unclosed quotes, and basic mapping syntax.
        """
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            if "\t" in line:
                return FileIntegrityResult(
                    path=filename,
                    valid=False,
                    language="yaml",
                    error_message=f"Tab character found in YAML indentation at line {idx}",
                    line=idx,
                    column=line.find("\t") + 1,
                    snippet=line,
                )
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            # Check unclosed quotes on single-line values
            single_quotes = stripped.count("'")
            double_quotes = stripped.count('"')
            if (single_quotes % 2 != 0) or (double_quotes % 2 != 0):
                # Only flag if not a multiline pipe or angle indicator
                if not stripped.endswith("|") and not stripped.endswith(">"):
                    return FileIntegrityResult(
                        path=filename,
                        valid=False,
                        language="yaml",
                        error_message=f"Unclosed string quote detected at line {idx}",
                        line=idx,
                        snippet=line,
                    )
        return FileIntegrityResult(path=filename, valid=True, language="yaml")

    @classmethod
    def check_toml(cls, content: str, filename: str = "<string>") -> FileIntegrityResult:
        """
        Lightweight TOML syntax verification:
        Checks unclosed brackets in table headers, unclosed strings, and bad assignment.
        """
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("["):
                if not stripped.endswith("]"):
                    return FileIntegrityResult(
                        path=filename,
                        valid=False,
                        language="toml",
                        error_message=f"Unclosed table header bracket at line {idx}",
                        line=idx,
                        snippet=line,
                    )
            elif "=" in stripped:
                single_quotes = stripped.count("'")
                double_quotes = stripped.count('"')
                if (single_quotes % 2 != 0) or (double_quotes % 2 != 0):
                    return FileIntegrityResult(
                        path=filename,
                        valid=False,
                        language="toml",
                        error_message=f"Unclosed quote in TOML assignment at line {idx}",
                        line=idx,
                        snippet=line,
                    )
        return FileIntegrityResult(path=filename, valid=True, language="toml")

    @classmethod
    def check_javascript(cls, content: str, filename: str = "<string>", lang: str = "javascript") -> FileIntegrityResult:
        """
        Balanced bracket and delimiter syntax validator for JS/TS.
        """
        stack = []
        pairs = {")": "(", "}": "{", "]": "["}
        lines = content.splitlines()

        in_multi_comment = False
        for line_idx, line in enumerate(lines, start=1):
            i = 0
            while i < len(line):
                if in_multi_comment:
                    if line[i:i+2] == "*/":
                        in_multi_comment = False
                        i += 2
                    else:
                        i += 1
                    continue

                if line[i:i+2] == "/*":
                    in_multi_comment = True
                    i += 2
                    continue
                if line[i:i+2] == "//":
                    break

                char = line[i]
                if char in "({[":
                    stack.append((char, line_idx, i + 1))
                elif char in ")}]":
                    expected = pairs[char]
                    if not stack or stack[-1][0] != expected:
                        return FileIntegrityResult(
                            path=filename,
                            valid=False,
                            language=lang,
                            error_message=f"Unmatched closing delimiter '{char}' at line {line_idx}, column {i+1}",
                            line=line_idx,
                            column=i + 1,
                            snippet=line,
                        )
                    stack.pop()
                i += 1

        if stack:
            open_char, op_line, op_col = stack[-1]
            return FileIntegrityResult(
                path=filename,
                valid=False,
                language=lang,
                error_message=f"Unclosed delimiter '{open_char}' opened at line {op_line}, column {op_col}",
                line=op_line,
                column=op_col,
                snippet=lines[op_line - 1] if 0 < op_line <= len(lines) else None,
            )

        return FileIntegrityResult(path=filename, valid=True, language=lang)

    @classmethod
    def check_markdown(cls, content: str, filename: str = "<string>") -> FileIntegrityResult:
        """
        Markdown code fence verification:
        Ensures code fences (``` or ~~~) are properly paired and closed.
        """
        lines = content.splitlines()
        in_fence = False
        fence_char = ""
        fence_line = 0

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                cur_char = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_char = cur_char
                    fence_line = idx
                elif cur_char == fence_char:
                    in_fence = False

        if in_fence:
            return FileIntegrityResult(
                path=filename,
                valid=False,
                language="markdown",
                error_message=f"Unclosed code fence '{fence_char}' opened at line {fence_line}",
                line=fence_line,
            )
        return FileIntegrityResult(path=filename, valid=True, language="markdown")

    @classmethod
    def check_content(cls, content: str, filename: str) -> FileIntegrityResult:
        lang = cls.detect_language(filename)
        if not lang:
            return FileIntegrityResult(path=filename, valid=True, language="plain_text")

        if lang == "python":
            return cls.check_python(content, filename)
        elif lang == "json":
            return cls.check_json(content, filename)
        elif lang == "yaml":
            return cls.check_yaml(content, filename)
        elif lang == "toml":
            return cls.check_toml(content, filename)
        elif lang in ("javascript", "typescript"):
            return cls.check_javascript(content, filename, lang)
        elif lang == "markdown":
            return cls.check_markdown(content, filename)
        return FileIntegrityResult(path=filename, valid=True, language=lang)

    @classmethod
    def check_file(cls, file_path: Union[str, Path]) -> FileIntegrityResult:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return FileIntegrityResult(
                path=str(p),
                valid=False,
                language="unknown",
                error_message=f"File '{file_path}' does not exist or is not a regular file.",
            )
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return cls.check_content(content, str(p))
        except Exception as e:
            return FileIntegrityResult(
                path=str(p),
                valid=False,
                language="unknown",
                error_message=f"Failed to read file: {str(e)}",
            )

    @classmethod
    def check_workspace(
        cls,
        root_dir: Union[str, Path] = ".",
        paths: Optional[List[Union[str, Path]]] = None,
        ignores: Optional[Set[str]] = None,
        strict: bool = False,
    ) -> IntegrityReport:
        """
        Audits workspace files for syntax integrity.
        """
        root = Path(root_dir).resolve()
        ignore_names = ignores or {
            ".git", ".rollback", "__pycache__", ".pytest_cache",
            ".venv", "venv", "node_modules", ".DS_Store", "build", "dist"
        }

        results: List[FileIntegrityResult] = []

        if paths:
            files_to_check = [root / p if not Path(p).is_absolute() else Path(p) for p in paths]
        else:
            files_to_check = []
            for cur_root, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in ignore_names]
                for file_name in files:
                    ext = Path(file_name).suffix.lower()
                    if ext in cls.EXTENSIONS:
                        files_to_check.append(Path(cur_root) / file_name)

        for file_path in sorted(files_to_check):
            if file_path.is_file():
                rel_path = str(file_path.relative_to(root)).replace("\\", "/") if file_path.is_relative_to(root) else str(file_path)
                res = cls.check_file(file_path)
                res.path = rel_path
                results.append(res)

        passed = sum(1 for r in results if r.valid)
        failed = sum(1 for r in results if not r.valid)
        report = IntegrityReport(
            files_checked=len(results),
            passed_count=passed,
            failed_count=failed,
            is_clean=(failed == 0),
            results=results,
        )

        if strict and failed > 0:
            err_details = "\n".join(f"- {f.path}: {f.error_message}" for f in report.failures)
            raise IntegrityError(f"Integrity check failed with {failed} errors:\n{err_details}")

        return report
