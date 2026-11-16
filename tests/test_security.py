import pytest
from pathlib import Path
from rollback.security import SecurityPolicy, SecurityViolationError
from rollback.core import RollbackSession


def test_security_policy_path_traversal(tmp_path: Path):
    policy = SecurityPolicy(allowed_root=tmp_path)
    
    # Valid relative and absolute paths within root
    valid_file = tmp_path / "valid.txt"
    valid_file.write_text("ok")
    assert policy.validate_target_path("valid.txt") == valid_file.resolve()
    assert policy.validate_target_path(valid_file) == valid_file.resolve()

    # Traversal outside root should raise SecurityViolationError
    with pytest.raises(SecurityViolationError, match="Path traversal detected"):
        policy.validate_target_path("../outside.txt")

    with pytest.raises(SecurityViolationError, match="Path traversal detected"):
        policy.validate_target_path(tmp_path.parent / "escape.txt")


def test_security_policy_protected_patterns(tmp_path: Path):
    policy = SecurityPolicy(allowed_root=tmp_path)
    
    with pytest.raises(SecurityViolationError, match="matches protected sensitive pattern"):
        policy.validate_target_path(".env")

    with pytest.raises(SecurityViolationError, match="matches protected sensitive pattern"):
        policy.validate_target_path("secrets.key")

    with pytest.raises(SecurityViolationError, match="matches protected sensitive pattern"):
        policy.validate_target_path("config/id_rsa.pub")


def test_session_with_security_policy_blocks_traversal(tmp_path: Path):
    policy = SecurityPolicy(allowed_root=tmp_path)
    session = RollbackSession(root_dir=str(tmp_path), security_policy=policy)

    with pytest.raises(SecurityViolationError):
        session.scan_workspace(target_files=["../../etc/passwd"])
