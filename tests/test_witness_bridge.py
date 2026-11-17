import os
import shutil
import tempfile
import pytest

from rollback.core import create_session
from rollback.diff import compute_diff
from rollback.witness_bridge import attest_checkpoint, attest_diff_summary, HAS_WITNESS


class MockStep:
    def __init__(self, index: int):
        self.index = index


class MockWitnessEngine:
    def __init__(self):
        self.steps = []

    def record_step(self, step_type, description, metadata=None):
        step = MockStep(len(self.steps))
        self.steps.append({"step_type": step_type, "description": description, "metadata": metadata})
        return step


class TestRollbackWitnessBridge:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp(prefix="rollback_witness_test_")
        self.file_path = os.path.join(self.temp_dir, "data.txt")
        with open(self.file_path, "w") as f:
            f.write("initial line 1\n")

    def teardown_method(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_attest_checkpoint_with_mock_engine(self):
        session = create_session(root_dir=self.temp_dir)
        cp = session.checkpoint("initial_checkpoint", target_files=[self.file_path])
        mock_eng = MockWitnessEngine()

        att_res = attest_checkpoint(cp, engine=mock_eng, agent_id="rollback-agent-01")
        assert att_res["attested"] is True
        assert att_res["checkpoint_id"] == cp.id
        assert att_res["step_index"] == 0
        assert len(mock_eng.steps) == 1
        assert "Rollback Checkpoint" in mock_eng.steps[0]["description"]

    def test_attest_diff_summary_with_mock_engine(self):
        session = create_session(root_dir=self.temp_dir)
        cp = session.checkpoint("initial", target_files=[self.file_path])
        with open(self.file_path, "a") as f:
            f.write("added line 2\n")

        diff = compute_diff(session, cp)
        mock_eng = MockWitnessEngine()

        att_res = attest_diff_summary(diff, engine=mock_eng, agent_id="rollback-agent-02")
        assert att_res["attested"] is True
        assert att_res["files_changed"] >= 1
        assert att_res["step_index"] == 0
        assert len(mock_eng.steps) == 1
        assert "Rollback Diff" in mock_eng.steps[0]["description"]

    def test_attest_checkpoint_environment_aware(self):
        session = create_session(root_dir=self.temp_dir)
        cp = session.checkpoint("auto_checkpoint", target_files=[self.file_path])

        att_res = attest_checkpoint(cp, agent_id="rollback-agent-env")
        if HAS_WITNESS:
            assert att_res["attested"] is True
            assert att_res["checkpoint_id"] == cp.id
        else:
            assert att_res["attested"] is False
            assert att_res["reason"] == "agent-witness not installed"
