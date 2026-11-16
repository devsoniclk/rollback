import tempfile
import time
from pathlib import Path
import pytest

from rollback.core import RollbackSession
from rollback.lease import LeaseConflictError, WorkspaceLeaseManager


def test_lease_acquire_and_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        lm = session.lease_manager

        # Agent 1 acquires exclusive lease on core.py and diff.py
        l1 = lm.acquire(agent_id="agent-atlas", paths=["core.py", "diff.py"], mode="exclusive", ttl_seconds=60)
        assert l1.agent_id == "agent-atlas"
        assert l1.mode == "exclusive"
        assert "core.py" in l1.paths
        assert l1.remaining_seconds > 0

        # Agent 2 tries to acquire exclusive lease overlapping on core.py -> Conflict
        with pytest.raises(LeaseConflictError) as exc_info:
            lm.acquire(agent_id="agent-forge", paths=["core.py", "utils.py"], mode="exclusive")
        assert len(exc_info.value.conflicting_leases) == 1
        assert exc_info.value.conflicting_leases[0]["agent_id"] == "agent-atlas"

        # Agent 2 tries to acquire shared lease overlapping on diff.py -> Conflict with exclusive
        with pytest.raises(LeaseConflictError):
            lm.acquire(agent_id="agent-forge", paths=["diff.py"], mode="shared")

        # Agent 2 acquires lease on non-overlapping path
        l2 = lm.acquire(agent_id="agent-forge", paths=["unrelated.py"], mode="exclusive")
        assert l2.agent_id == "agent-forge"


def test_lease_shared_concurrency():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        lm = session.lease_manager

        # Multiple agents can acquire shared leases on the same paths
        s1 = lm.acquire(agent_id="reader-1", paths=["docs.md"], mode="shared")
        s2 = lm.acquire(agent_id="reader-2", paths=["docs.md"], mode="shared")
        assert s1.mode == "shared"
        assert s2.mode == "shared"

        # But exclusive writer cannot acquire while readers hold shared lease
        with pytest.raises(LeaseConflictError):
            lm.acquire(agent_id="writer-1", paths=["docs.md"], mode="exclusive")


def test_lease_renewal_and_release():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        lm = session.lease_manager

        lease = lm.acquire(agent_id="agent-worker", paths=["task.py"], ttl_seconds=10)
        token = lease.token

        renewed = lm.renew(token, additional_seconds=50)
        assert renewed.remaining_seconds > 20

        # Release lease
        released = lm.release(token)
        assert released is True

        # Now another agent can acquire immediately
        new_l = lm.acquire(agent_id="agent-other", paths=["task.py"], mode="exclusive")
        assert new_l.agent_id == "agent-other"


def test_lease_expiration_cleanup():
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        lm = session.lease_manager

        # Create lease with 0 second TTL (instantly expired)
        l_exp = lm.acquire(agent_id="agent-fast", paths=["temp.py"], ttl_seconds=0.0)
        time.sleep(0.05)

        assert l_exp.is_expired is True
        active = lm.list_active()
        assert len(active) == 0

        # Re-acquiring should succeed because prior lease is expired
        l_next = lm.acquire(agent_id="agent-slow", paths=["temp.py"], ttl_seconds=30)
        assert l_next.agent_id == "agent-slow"
