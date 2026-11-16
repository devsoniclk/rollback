"""
Multi-Agent Workspace Lease & Concurrency Coordinator for agent swarms.

Provides file-level shared (read) and exclusive (write) leases with TTLs, heartbeat renewal,
and conflict detection to prevent overlapping agent edits in multi-agent environments.
"""

import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class LeaseConflictError(Exception):
    """Raised when an agent attempts to acquire a lease on already locked paths."""

    def __init__(self, message: str, conflicting_leases: List[Dict[str, Any]]):
        super().__init__(message)
        self.conflicting_leases = conflicting_leases


class WorkspaceLease:
    """Represents an active path lease held by an AI agent."""

    def __init__(
        self,
        lease_id: str,
        agent_id: str,
        paths: List[str],
        mode: str,
        created_at: float,
        expires_at: float,
        token: str,
    ):
        self.lease_id = lease_id
        self.agent_id = agent_id
        self.paths = sorted(list(set(paths)))
        self.mode = mode.lower()  # "exclusive" or "shared"
        self.created_at = created_at
        self.expires_at = expires_at
        self.token = token

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def to_dict(self, include_token: bool = False) -> Dict[str, Any]:
        data = {
            "lease_id": self.lease_id,
            "agent_id": self.agent_id,
            "paths": self.paths,
            "mode": self.mode,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "remaining_seconds": round(self.remaining_seconds, 1),
            "is_expired": self.is_expired,
        }
        if include_token:
            data["token"] = self.token
        return data

    def __repr__(self) -> str:
        return (
            f"<WorkspaceLease {self.lease_id} agent='{self.agent_id}' "
            f"mode={self.mode} paths={len(self.paths)} ttl={round(self.remaining_seconds)}s>"
        )


class WorkspaceLeaseManager:
    """Manages multi-agent file access leases backed by .rollback/leases.json."""

    def __init__(self, session: Any):
        self.session = session
        self.leases_file: Path = self.session.rollback_dir / "leases.json"

    def _normalize_path(self, p: Union[str, Path]) -> str:
        path = Path(p)
        if path.is_absolute():
            try:
                path = path.relative_to(self.session.workspace_dir)
            except ValueError:
                pass
        return str(path).replace("\\", "/").strip("/")

    def _load_leases(self) -> List[WorkspaceLease]:
        if not self.leases_file.exists():
            return []
        try:
            with open(self.leases_file, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
            return [
                WorkspaceLease(
                    lease_id=item["lease_id"],
                    agent_id=item["agent_id"],
                    paths=item["paths"],
                    mode=item.get("mode", "exclusive"),
                    created_at=item["created_at"],
                    expires_at=item["expires_at"],
                    token=item["token"],
                )
                for item in raw_list
            ]
        except Exception:
            return []

    def _save_leases(self, leases: List[WorkspaceLease]) -> None:
        self.leases_file.parent.mkdir(parents=True, exist_ok=True)
        raw_list = [
            {
                "lease_id": l.lease_id,
                "agent_id": l.agent_id,
                "paths": l.paths,
                "mode": l.mode,
                "created_at": l.created_at,
                "expires_at": l.expires_at,
                "token": l.token,
            }
            for l in leases
        ]
        with open(self.leases_file, "w", encoding="utf-8") as f:
            json.dump(raw_list, f, indent=2)

    def clean_expired(self) -> int:
        """Purge all expired leases from the database. Returns count of purged leases."""
        leases = self._load_leases()
        active = [l for l in leases if not l.is_expired]
        purged = len(leases) - len(active)
        if purged > 0:
            self._save_leases(active)
        return purged

    def acquire(
        self,
        agent_id: str,
        paths: List[Union[str, Path]],
        mode: str = "exclusive",
        ttl_seconds: float = 300.0,
    ) -> WorkspaceLease:
        """
        Acquire a path-level lease for an agent.
        Mode can be 'exclusive' (write lock) or 'shared' (read lock).
        Raises LeaseConflictError if a conflicting non-expired lease exists.
        """
        self.clean_expired()
        leases = self._load_leases()
        active_leases = [l for l in leases if not l.is_expired]

        target_paths = set(self._normalize_path(p) for p in paths)
        mode_norm = mode.lower()
        if mode_norm not in ("exclusive", "shared"):
            raise ValueError("Mode must be 'exclusive' or 'shared'")

        conflicts = []
        for lease in active_leases:
            overlap = set(lease.paths).intersection(target_paths)
            if overlap:
                # Exclusive conflicts with everything from other agents; shared conflicts with exclusive
                if lease.agent_id != agent_id:
                    if mode_norm == "exclusive" or lease.mode == "exclusive":
                        conflicts.append(
                            {
                                "lease_id": lease.lease_id,
                                "agent_id": lease.agent_id,
                                "overlapping_paths": sorted(list(overlap)),
                                "mode": lease.mode,
                                "expires_at": lease.expires_at,
                            }
                        )

        if conflicts:
            conflict_msg = (
                f"Path lease conflict for agent '{agent_id}': requested {len(target_paths)} paths, "
                f"conflicts with {len(conflicts)} active lease(s)."
            )
            raise LeaseConflictError(conflict_msg, conflicts)

        now = time.time()
        new_lease = WorkspaceLease(
            lease_id=f"lease_{uuid.uuid4().hex[:10]}",
            agent_id=agent_id,
            paths=sorted(list(target_paths)),
            mode=mode_norm,
            created_at=now,
            expires_at=now + ttl_seconds,
            token=secrets.token_hex(16),
        )

        active_leases.append(new_lease)
        self._save_leases(active_leases)
        return new_lease

    def renew(self, token: str, additional_seconds: float = 300.0) -> WorkspaceLease:
        """Heartbeat renewal: extend remaining TTL of an active lease."""
        leases = self._load_leases()
        for lease in leases:
            if lease.token == token:
                if lease.is_expired:
                    raise ValueError(f"Cannot renew expired lease '{lease.lease_id}'")
                lease.expires_at = time.time() + additional_seconds
                self._save_leases(leases)
                return lease
        raise KeyError(f"Lease with token '{token}' not found")

    def release(self, token: str) -> bool:
        """Release a lease using its secret token."""
        leases = self._load_leases()
        kept = [l for l in leases if l.token != token]
        if len(kept) != len(leases):
            self._save_leases(kept)
            return True
        return False

    def revoke(self, lease_id: str) -> bool:
        """Administrative force-revocation by lease_id."""
        leases = self._load_leases()
        kept = [l for l in leases if l.lease_id != lease_id]
        if len(kept) != len(leases):
            self._save_leases(kept)
            return True
        return False

    def list_active(self) -> List[WorkspaceLease]:
        """List all active non-expired leases."""
        self.clean_expired()
        return [l for l in self._load_leases() if not l.is_expired]
