import os
import shutil
import tempfile
import pytest

from rollback.core import create_session
from rollback.diff import compute_diff
from rollback.witness_bridge import attest_checkpoint, attest_diff_summary


class TestRollbackWitnessBridge:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp(prefix="rollback_witness_test_")
        self.file_path = os.path.join(self.temp_dir, "data.txt")
        with open(self.file_path, "w") as f:
            f.write("initial line 1\n")

    def teardown_method(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_attest_checkpoint(self):
        session = create_session(root_dir=self.temp_dir)
        cp = session.checkpoint("initial_checkpoint", target_files=[self.file_path])

        att_res = attest_checkpoint(cp, agent_id="rollback-agent-01")
        assert att_res["attested"] is True
        assert att_res["checkpoint_id"] == cp.id
        assert att_res["step_index"] >= 0

    def test_attest_diff_summary(self):
        session = create_session(root_dir=self.temp_dir)
        cp = session.checkpoint("initial", target_files=[self.file_path])
        with open(self.file_path, "a") as f:
            f.write("added line 2\n")

        diff = compute_diff(session, cp)
        att_res = attest_diff_summary(diff, agent_id="rollback-agent-02")
        assert att_res["attested"] is True
        assert att_res["files_changed"] >= 1
