"""
Multi-Agent Optimistic Concurrency Control (OCC) and Multi-Version Concurrency (MVCC) for rollback.
Enables parallel agent collaboration with fine-grained conflict detection, isolation,
and atomic commit verification.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class TransactionConflictError(Exception):
    """Raised when an OCC transaction encounters a write-write or write-read conflict."""

    def __init__(self, message: str, conflicts: List[Dict[str, Any]]):
        super().__init__(message)
        self.conflicts = conflicts


class MVCCManager:
    """
    Coordinates multi-version concurrency control across concurrent agent operations.
    Maintains committed version snapshots and verifies read-set validity prior to write application.
    """

    def __init__(self, root_dir: Union[str, Path]):
        self.root_dir = Path(root_dir).resolve()
        self._committed_versions: Dict[str, str] = {}  # relative_path -> sha256_hash
        self._active_transactions: Dict[str, Dict[str, Any]] = {}
        self._scan_initial_state()

    def _hash_file(self, path: Path) -> str:
        if not path.is_file():
            return "NON_EXISTENT"
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def _scan_initial_state(self) -> None:
        """Indexes current disk files to establish baseline committed versions."""
        if not self.root_dir.is_dir():
            return
        for root, _, files in os.walk(self.root_dir):
            if ".rollback" in root or ".git" in root:
                continue
            for fname in files:
                fpath = Path(root) / fname
                rel = str(fpath.relative_to(self.root_dir))
                self._committed_versions[rel] = self._hash_file(fpath)

    def begin_transaction(self, agent_id: str) -> str:
        """
        Starts an OCC transaction for an agent, capturing a snapshot view of committed versions.
        """
        tx_id = f"tx-{uuid.uuid4().hex[:8]}"
        self._active_transactions[tx_id] = {
            "agent_id": agent_id,
            "start_time": time.time(),
            "snapshot_versions": dict(self._committed_versions),
            "read_set": {},    # rel_path -> observed_hash
            "write_set": {},   # rel_path -> new_content_bytes
            "status": "active",
        }
        return tx_id

    def record_read(self, tx_id: str, relative_path: str) -> str:
        """Records a path read in the transaction's read set."""
        tx = self._get_tx(tx_id)
        fpath = self.root_dir / relative_path
        current_hash = self._hash_file(fpath)
        tx["read_set"][relative_path] = current_hash
        return current_hash

    def stage_write(self, tx_id: str, relative_path: str, content: Union[str, bytes]) -> None:
        """Stages a file modification within the transaction buffer."""
        tx = self._get_tx(tx_id)
        data = content.encode("utf-8") if isinstance(content, str) else content
        tx["write_set"][relative_path] = data
        if relative_path not in tx["read_set"]:
            fpath = self.root_dir / relative_path
            tx["read_set"][relative_path] = self._hash_file(fpath)

    def validate_and_commit(self, tx_id: str) -> Dict[str, Any]:
        """
        Validates that no concurrent commits have invalidated the read-set.
        Applies writes atomically if valid; raises TransactionConflictError otherwise.
        """
        tx = self._get_tx(tx_id)
        conflicts = []

        # 1. Check read-set against current committed state
        for rel_path, read_hash in tx["read_set"].items():
            fpath = self.root_dir / rel_path
            current_disk_hash = self._hash_file(fpath)
            committed_hash = self._committed_versions.get(rel_path, "NON_EXISTENT")

            if current_disk_hash != read_hash or committed_hash != read_hash:
                conflicts.append({
                    "path": rel_path,
                    "expected_hash": read_hash,
                    "disk_hash": current_disk_hash,
                    "committed_hash": committed_hash,
                    "type": "WRITE_WRITE_CONFLICT" if rel_path in tx["write_set"] else "STALE_READ_CONFLICT",
                })

        if conflicts:
            tx["status"] = "conflicted"
            raise TransactionConflictError(
                f"Transaction '{tx_id}' encountered {len(conflicts)} OCC conflict(s).",
                conflicts=conflicts,
            )

        # 2. Apply writes atomically
        written_paths = []
        for rel_path, content_bytes in tx["write_set"].items():
            fpath = self.root_dir / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = fpath.with_suffix(f"{fpath.suffix}.tmp.{uuid.uuid4().hex[:6]}")
            with open(tmp_path, "wb") as f:
                f.write(content_bytes)
            os.replace(tmp_path, fpath)

            new_hash = self._hash_file(fpath)
            self._committed_versions[rel_path] = new_hash
            written_paths.append(rel_path)

        tx["status"] = "committed"
        res = {
            "tx_id": tx_id,
            "status": "committed",
            "agent_id": tx["agent_id"],
            "written_files": written_paths,
            "commit_time": time.time(),
        }
        del self._active_transactions[tx_id]
        return res

    def abort_transaction(self, tx_id: str) -> Dict[str, Any]:
        """Discards transaction and clears staging buffers."""
        if tx_id in self._active_transactions:
            tx = self._active_transactions.pop(tx_id)
            return {"tx_id": tx_id, "status": "aborted", "agent_id": tx["agent_id"]}
        return {"tx_id": tx_id, "status": "not_found"}

    def _get_tx(self, tx_id: str) -> Dict[str, Any]:
        if tx_id not in self._active_transactions:
            raise KeyError(f"Transaction '{tx_id}' not found or already closed.")
        tx = self._active_transactions[tx_id]
        if tx["status"] != "active":
            raise ValueError(f"Transaction '{tx_id}' is in inactive state: {tx['status']}.")
        return tx
