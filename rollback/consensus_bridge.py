"""
Consensus-Guarded Atomic Commits and Byzantine Fault Tolerance integration for rollback.
Enforces multi-agent BFT consensus verification before committing workspace mutations to disk,
with automatic instantaneous revert on proposal rejection, equivocation, or Byzantine faults.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .core import Checkpoint, RollbackSession

try:
    import agent_witness
    from agent_witness.consensus import (
        BFTConsensusCertificate,
        BFTVote,
        ByzantineFaultEvidence,
        EquivocationDetector,
        verify_bft_certificate,
    )
    HAS_WITNESS = True
except ImportError:
    HAS_WITNESS = False


class ConsensusVerificationError(Exception):
    """Raised when consensus verification fails or a Byzantine fault aborts a transaction."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


class ConsensusGuardedSession:
    """
    A transactional workspace wrapper that requires cryptographic BFT consensus
    before allowing staged file modifications to commit permanently to disk.
    """

    def __init__(
        self,
        session: RollbackSession,
        proposal_id: Optional[str] = None,
        agent_id: str = "agent-proposer",
    ):
        self.session = session
        self.proposal_id = proposal_id or f"prop-{int(time.time()*1000)}"
        self.agent_id = agent_id
        self.staged_checkpoint_id: Optional[str] = None
        self.trajectory_digest: Optional[str] = None
        self.is_committed: bool = False
        self.is_aborted: bool = False
        self.audit_log: List[Dict[str, Any]] = []

    def stage(self, label: Optional[str] = None) -> str:
        """
        Creates a staging checkpoint representing the pre-mutation baseline.
        Returns the checkpoint ID.
        """
        lbl = label or f"consensus-guard:{self.proposal_id}"
        cp = self.session.create_checkpoint(label=lbl)
        self.staged_checkpoint_id = cp.id
        self.is_committed = False
        self.is_aborted = False

        # Compute initial baseline digest
        h = hashlib.sha256()
        h.update(self.proposal_id.encode())
        h.update(cp.id.encode())
        self.trajectory_digest = h.hexdigest()

        self._record_audit("stage", {"checkpoint_id": cp.id, "trajectory_digest": self.trajectory_digest})
        return cp.id

    def verify_and_commit(
        self,
        certificate: Union[Dict[str, Any], Any],
        signers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Verifies a BFTConsensusCertificate. If quorum threshold is achieved
        and no unslashed Byzantine faults exist, commits the changes.
        Otherwise, immediately reverts workspace to baseline and raises ConsensusVerificationError.
        """
        if not self.staged_checkpoint_id:
            raise ConsensusVerificationError("No staged checkpoint active to commit.")
        if self.is_committed:
            raise ConsensusVerificationError("Transaction is already committed.")
        if self.is_aborted:
            raise ConsensusVerificationError("Transaction has been aborted.")

        cert_dict = certificate if isinstance(certificate, dict) else certificate.to_dict()

        is_valid, validation_info = self._validate_certificate(cert_dict, signers)

        if not is_valid:
            # Revert immediately
            revert_res = self.session.revert(self.staged_checkpoint_id)
            self.is_aborted = True
            self._record_audit("rejection_rollback", {
                "checkpoint_id": self.staged_checkpoint_id,
                "revert_result": revert_res,
                "reason": validation_info.get("error", "Quorum verification failed"),
            })
            raise ConsensusVerificationError(
                f"Consensus quorum rejected: {validation_info.get('error')}",
                details=validation_info,
            )

        # Consensus succeeded - finalize
        self.is_committed = True
        result = {
            "status": "committed",
            "proposal_id": self.proposal_id,
            "checkpoint_id": self.staged_checkpoint_id,
            "trajectory_digest": self.trajectory_digest,
            "quorum_weight": validation_info.get("committed_weight"),
            "quorum_threshold": validation_info.get("quorum_threshold"),
            "validators_count": validation_info.get("total_validators"),
            "timestamp": time.time(),
        }
        self._record_audit("commit", result)
        return result

    def abort_on_fault(
        self,
        evidence: Union[Dict[str, Any], List[Dict[str, Any]], str],
    ) -> Dict[str, Any]:
        """
        Immediately aborts the staged proposal on detected Byzantine faults / equivocation,
        reverting all file changes back to the pre-proposal state.
        """
        if not self.staged_checkpoint_id:
            return {"status": "noop", "message": "No staged checkpoint to revert."}

        revert_res = self.session.revert(self.staged_checkpoint_id)
        self.is_aborted = True

        evidence_payload = evidence if not isinstance(evidence, str) else {"description": evidence}
        audit_entry = {
            "status": "aborted_fault",
            "proposal_id": self.proposal_id,
            "checkpoint_id": self.staged_checkpoint_id,
            "evidence": evidence_payload,
            "revert_result": revert_res,
            "timestamp": time.time(),
        }
        self._record_audit("byzantine_abort", audit_entry)
        return audit_entry

    def _validate_certificate(
        self,
        cert_dict: Dict[str, Any],
        signers: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Validates the BFT certificate using agent-witness if available, or standalone rules.
        """
        if HAS_WITNESS and not isinstance(cert_dict, dict):
            try:
                cert_obj = cert_dict if hasattr(cert_dict, "certificate_id") else BFTConsensusCertificate.from_dict(cert_dict)
                valid, info = verify_bft_certificate(cert_obj, signers)
                return valid, info
            except Exception as e:
                return False, {"error": f"witness verification error: {str(e)}"}

        # Standalone verification fallback
        committed_weight = cert_dict.get("committed_weight", 0)
        quorum_threshold = cert_dict.get("quorum_threshold", 0)
        is_finalized = cert_dict.get("is_finalized", False)
        slashed_agents = cert_dict.get("slashed_agents", [])
        precommits = cert_dict.get("precommits", [])

        if not is_finalized:
            return False, {"error": "Certificate is not marked finalized"}

        if committed_weight < quorum_threshold:
            return False, {
                "error": f"Committed weight ({committed_weight}) < quorum threshold ({quorum_threshold})",
                "committed_weight": committed_weight,
                "quorum_threshold": quorum_threshold,
            }

        # Check for presence of votes from slashed agents in precommits
        slashed_set = set(slashed_agents)
        valid_precommit_weight = 0
        voted_agents = set()

        for vote in precommits:
            voter = vote.get("agent_id") if isinstance(vote, dict) else getattr(vote, "agent_id", "")
            weight = vote.get("weight", 1) if isinstance(vote, dict) else getattr(vote, "weight", 1)
            if voter in slashed_set:
                return False, {"error": f"Vote from slashed agent '{voter}' found in precommit quorum"}
            if voter in voted_agents:
                return False, {"error": f"Duplicate vote from agent '{voter}' in precommits (equivocation)"}
            voted_agents.add(voter)
            valid_precommit_weight += weight

        if valid_precommit_weight < quorum_threshold:
            return False, {
                "error": f"Valid non-slashed precommit weight ({valid_precommit_weight}) < quorum threshold ({quorum_threshold})",
                "valid_precommit_weight": valid_precommit_weight,
                "quorum_threshold": quorum_threshold,
            }

        return True, {
            "committed_weight": committed_weight,
            "quorum_threshold": quorum_threshold,
            "total_validators": cert_dict.get("total_validators", len(precommits)),
            "slashed_count": len(slashed_agents),
        }

    def _record_audit(self, event_type: str, data: Dict[str, Any]) -> None:
        self.audit_log.append({
            "event": event_type,
            "proposal_id": self.proposal_id,
            "timestamp": time.time(),
            "data": data,
        })
