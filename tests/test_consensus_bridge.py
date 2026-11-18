import os
import shutil
import tempfile
import pytest

from rollback.core import create_session
from rollback.consensus_bridge import (
    ConsensusGuardedSession,
    ConsensusVerificationError,
)
from rollback.concurrency import (
    MVCCManager,
    TransactionConflictError,
)
from rollback.mcp_server import RollbackMCPServer


class TestRollbackConsensusBridge:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp(prefix="rollback_con_test_")
        self.file_path = os.path.join(self.temp_dir, "config.json")
        with open(self.file_path, "w") as f:
            f.write("{\"version\": 1, \"status\": \"stable\"}\n")

    def teardown_method(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_honest_consensus_commit(self):
        session = create_session(root_dir=self.temp_dir)
        guarded = ConsensusGuardedSession(session, proposal_id="prop-test-01")
        cp_id = guarded.stage()
        assert cp_id is not None
        assert guarded.trajectory_digest is not None

        # Mutate file
        with open(self.file_path, "w") as f:
            f.write("{\"version\": 2, \"status\": \"upgraded\"}\n")

        # Valid BFT certificate with 3 validators, quorum = 3, weight = 3
        cert = {
            "certificate_id": "cert-1",
            "trajectory_id": "traj-1",
            "merkle_root": "0x1234abcd",
            "round": 1,
            "total_validators": 4,
            "byzantine_threshold_f": 1,
            "quorum_threshold": 3,
            "total_weight": 4,
            "committed_weight": 3,
            "is_finalized": True,
            "prevotes": [
                {"agent_id": "v1", "weight": 1},
                {"agent_id": "v2", "weight": 1},
                {"agent_id": "v3", "weight": 1},
            ],
            "precommits": [
                {"agent_id": "v1", "weight": 1},
                {"agent_id": "v2", "weight": 1},
                {"agent_id": "v3", "weight": 1},
            ],
            "slashed_agents": [],
        }

        res = guarded.verify_and_commit(cert)
        assert res["status"] == "committed"
        assert res["proposal_id"] == "prop-test-01"
        assert guarded.is_committed is True

        # Verify mutated content remains on disk
        with open(self.file_path, "r") as f:
            assert "upgraded" in f.read()

    def test_rejection_triggers_automatic_revert(self):
        session = create_session(root_dir=self.temp_dir)
        guarded = ConsensusGuardedSession(session, proposal_id="prop-reject-02")
        cp_id = guarded.stage()

        # Mutate file
        with open(self.file_path, "w") as f:
            f.write("{\"version\": 999, \"status\": \"corrupted_mutation\"}\n")

        # Under-threshold certificate: weight 2 < quorum 3
        rejected_cert = {
            "certificate_id": "cert-bad",
            "trajectory_id": "traj-bad",
            "merkle_root": "0xdeadbeef",
            "round": 1,
            "total_validators": 4,
            "byzantine_threshold_f": 1,
            "quorum_threshold": 3,
            "total_weight": 4,
            "committed_weight": 2,  # insufficient
            "is_finalized": True,
            "prevotes": [{"agent_id": "v1", "weight": 1}],
            "precommits": [{"agent_id": "v1", "weight": 1}, {"agent_id": "v2", "weight": 1}],
            "slashed_agents": [],
        }

        with pytest.raises(ConsensusVerificationError) as exc_info:
            guarded.verify_and_commit(rejected_cert)

        assert "Quorum" in str(exc_info.value) or "rejected" in str(exc_info.value)
        assert guarded.is_aborted is True

        # Verify disk was automatically reverted back to pristine state!
        with open(self.file_path, "r") as f:
            content = f.read()
            assert "stable" in content
            assert "corrupted_mutation" not in content

    def test_slashed_agent_vote_rejection(self):
        session = create_session(root_dir=self.temp_dir)
        guarded = ConsensusGuardedSession(session, proposal_id="prop-slashed-03")
        guarded.stage()

        with open(self.file_path, "w") as f:
            f.write("{\"version\": 3, \"status\": \"rogue_mutation\"}\n")

        cert = {
            "certificate_id": "cert-slashed",
            "trajectory_id": "traj-slashed",
            "merkle_root": "0xfeefee",
            "round": 1,
            "total_validators": 4,
            "byzantine_threshold_f": 1,
            "quorum_threshold": 3,
            "committed_weight": 3,
            "is_finalized": True,
            "precommits": [
                {"agent_id": "v1", "weight": 1},
                {"agent_id": "v2", "weight": 1},
                {"agent_id": "rogue-agent", "weight": 1},  # Slashed!
            ],
            "slashed_agents": ["rogue-agent"],
        }

        with pytest.raises(ConsensusVerificationError):
            guarded.verify_and_commit(cert)

        # Confirm reverted
        with open(self.file_path, "r") as f:
            assert "stable" in f.read()

    def test_abort_on_byzantine_fault(self):
        session = create_session(root_dir=self.temp_dir)
        guarded = ConsensusGuardedSession(session, proposal_id="prop-fault-04")
        guarded.stage()

        with open(self.file_path, "w") as f:
            f.write("{\"danger\": true}\n")

        evidence = {"agent_id": "bad_validator", "fault_type": "DOUBLE_VOTE", "round": 1}
        abort_res = guarded.abort_on_fault(evidence)

        assert abort_res["status"] == "aborted_fault"
        assert abort_res["evidence"] == evidence

        # File is pristine
        with open(self.file_path, "r") as f:
            assert "stable" in f.read()


class TestRollbackConcurrencyOCC:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp(prefix="rollback_occ_test_")
        self.file_a = os.path.join(self.temp_dir, "file_a.txt")
        self.file_b = os.path.join(self.temp_dir, "file_b.txt")
        with open(self.file_a, "w") as f:
            f.write("A0\n")
        with open(self.file_b, "w") as f:
            f.write("B0\n")

    def teardown_method(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_serializable_occ_commit(self):
        mvcc = MVCCManager(self.temp_dir)
        tx1 = mvcc.begin_transaction("agent-alice")
        mvcc.record_read(tx1, "file_a.txt")
        mvcc.stage_write(tx1, "file_a.txt", "A1_by_alice\n")

        res = mvcc.validate_and_commit(tx1)
        assert res["status"] == "committed"
        assert "file_a.txt" in res["written_files"]

        with open(self.file_a, "r") as f:
            assert f.read() == "A1_by_alice\n"

    def test_write_write_conflict_detection(self):
        mvcc = MVCCManager(self.temp_dir)
        tx1 = mvcc.begin_transaction("agent-alice")
        tx2 = mvcc.begin_transaction("agent-bob")

        # Both read file_b.txt at baseline B0
        mvcc.record_read(tx1, "file_b.txt")
        mvcc.record_read(tx2, "file_b.txt")

        # Alice stages write and commits first
        mvcc.stage_write(tx1, "file_b.txt", "B_alice\n")
        mvcc.validate_and_commit(tx1)

        # Bob stages conflicting write to the same file
        mvcc.stage_write(tx2, "file_b.txt", "B_bob\n")

        # Bob's commit should fail due to conflict
        with pytest.raises(TransactionConflictError) as exc_info:
            mvcc.validate_and_commit(tx2)

        assert len(exc_info.value.conflicts) == 1
        assert exc_info.value.conflicts[0]["path"] == "file_b.txt"


class TestRollbackMCPServerV10:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp(prefix="rollback_mcp_v10_")
        self.server = RollbackMCPServer(self.temp_dir)

    def teardown_method(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_tools_count_29(self):
        tools = self.server.get_tool_definitions()
        assert len(tools) == 29
        names = {t["name"] for t in tools}
        assert "rollback_attest_checkpoint" in names
        assert "rollback_consensus_guard_begin" in names
        assert "rollback_consensus_guard_commit" in names
        assert "rollback_consensus_guard_abort" in names
        assert "rollback_occ_transaction" in names

    def test_consensus_guard_mcp_workflow(self):
        import json
        # 1. Begin
        begin_res = json.loads(self.server.execute_tool("rollback_consensus_guard_begin", {
            "proposal_id": "prop-mcp-1",
            "agent_id": "test-agent",
        }))
        assert begin_res["status"] == "success"
        assert begin_res["proposal_id"] == "prop-mcp-1"

        # 2. Commit with valid cert
        cert = {
            "certificate_id": "cert-mcp",
            "trajectory_id": "traj-mcp",
            "round": 1,
            "total_validators": 3,
            "byzantine_threshold_f": 0,
            "quorum_threshold": 2,
            "committed_weight": 2,
            "is_finalized": True,
            "precommits": [{"agent_id": "a1", "weight": 1}, {"agent_id": "a2", "weight": 1}],
            "slashed_agents": [],
        }
        commit_res = json.loads(self.server.execute_tool("rollback_consensus_guard_commit", {
            "proposal_id": "prop-mcp-1",
            "certificate": cert,
        }))
        assert commit_res["status"] == "success"
        assert commit_res["commit"]["status"] == "committed"

    def test_occ_transaction_mcp(self):
        import json
        fpath = os.path.join(self.temp_dir, "occ_doc.txt")
        with open(fpath, "w") as f:
            f.write("Initial OCC content\n")

        res = json.loads(self.server.execute_tool("rollback_occ_transaction", {
            "agent_id": "agent-1",
            "reads": ["occ_doc.txt"],
            "writes": {"occ_doc.txt": "Updated OCC content\n"},
        }))
        assert res["status"] == "success"
        assert "occ_doc.txt" in res["transaction"]["written_files"]
