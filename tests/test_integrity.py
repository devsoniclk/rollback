import tempfile
import shutil
from pathlib import Path
import pytest

from rollback.integrity import IntegrityGate, IntegrityError, FileIntegrityResult


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_rollback_integrity_")
    yield Path(tmp_dir)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_python_syntax_checking():
    valid_code = "def add(x: int, y: int) -> int:\n    return x + y\n"
    res_valid = IntegrityGate.check_content(valid_code, "test.py")
    assert res_valid.valid is True
    assert res_valid.language == "python"

    invalid_code = "def broken(:\n    pass\n"
    res_invalid = IntegrityGate.check_content(invalid_code, "test.py")
    assert res_invalid.valid is False
    assert res_invalid.language == "python"
    assert res_invalid.line == 1
    assert "SyntaxError" in (res_invalid.error_message or "") or "invalid syntax" in (res_invalid.error_message or "")


def test_json_syntax_checking():
    valid_json = '{"name": "rollback", "version": "0.6.0", "active": true}'
    res_valid = IntegrityGate.check_content(valid_json, "config.json")
    assert res_valid.valid is True
    assert res_valid.language == "json"

    invalid_json = '{"name": "rollback", "version": 0.6.0, trailing,}'
    res_invalid = IntegrityGate.check_content(invalid_json, "config.json")
    assert res_invalid.valid is False
    assert res_invalid.language == "json"
    assert res_invalid.line is not None


def test_yaml_syntax_checking():
    valid_yaml = "version: 1\nservices:\n  web:\n    port: 8080\n"
    res_valid = IntegrityGate.check_content(valid_yaml, "deploy.yaml")
    assert res_valid.valid is True

    # Tab indentation in YAML
    tab_yaml = "services:\n\tweb: 8080\n"
    res_tab = IntegrityGate.check_content(tab_yaml, "deploy.yaml")
    assert res_tab.valid is False
    assert "Tab character" in (res_tab.error_message or "")


def test_toml_syntax_checking():
    valid_toml = "[package]\nname = 'rollback'\nversion = '0.6.0'\n"
    res_valid = IntegrityGate.check_content(valid_toml, "pyproject.toml")
    assert res_valid.valid is True

    invalid_toml = "[package\nname = 'rollback'\n"
    res_invalid = IntegrityGate.check_content(invalid_toml, "pyproject.toml")
    assert res_invalid.valid is False
    assert "Unclosed table header" in (res_invalid.error_message or "")


def test_javascript_delimiter_checking():
    valid_js = "function test() { const arr = [1, 2, 3]; return { val: arr[0] }; }"
    res_valid = IntegrityGate.check_content(valid_js, "app.ts")
    assert res_valid.valid is True

    invalid_js = "function test() { const arr = [1, 2, 3; }"
    res_invalid = IntegrityGate.check_content(invalid_js, "app.ts")
    assert res_invalid.valid is False
    assert "Unmatched closing delimiter" in (res_invalid.error_message or "")


def test_markdown_fence_checking():
    valid_md = "# Title\n\n```python\nprint('hello')\n```\n"
    res_valid = IntegrityGate.check_content(valid_md, "README.md")
    assert res_valid.valid is True

    invalid_md = "# Title\n\n```python\nprint('hello')\n"
    res_invalid = IntegrityGate.check_content(invalid_md, "README.md")
    assert res_invalid.valid is False
    assert "Unclosed code fence" in (res_invalid.error_message or "")


def test_workspace_audit_and_strict_mode(temp_workspace):
    (temp_workspace / "valid.py").write_text("print('clean')\n")
    (temp_workspace / "valid.json").write_text('{"status": "ok"}')
    (temp_workspace / "broken.py").write_text("def error(\n")

    report = IntegrityGate.check_workspace(root_dir=temp_workspace)
    assert report.files_checked == 3
    assert report.passed_count == 2
    assert report.failed_count == 1
    assert report.is_clean is False

    with pytest.raises(IntegrityError):
        IntegrityGate.check_workspace(root_dir=temp_workspace, strict=True)
