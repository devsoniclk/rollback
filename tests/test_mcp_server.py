from __future__ import annotations

import json
import tempfile
from pathlib import Path
from rollback.mcp_server import RollbackMCPServer


def test_mcp_server_protocol_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        server = RollbackMCPServer(root_dir=tmpdir)

        # 1. Initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
        init_resp = server.handle_request(init_req)
        assert init_resp is not None
        assert init_resp["id"] == 1
        assert init_resp["result"]["serverInfo"]["name"] == "rollback-engine"
        assert init_resp["result"]["serverInfo"]["version"] == "0.4.0"

        # 2. Ping
        ping_resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert ping_resp is not None
        assert ping_resp["id"] == 2

        # 3. List tools
        tools_resp = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert tools_resp is not None
        tool_names = [t["name"] for t in tools_resp["result"]["tools"]]
        assert "rollback_snapshot" in tool_names
        assert "rollback_revert" in tool_names
        assert "rollback_plan" in tool_names
        assert "rollback_diff" in tool_names
        assert "rollback_branch" in tool_names
        assert "rollback_security_check" in tool_names
        assert "rollback_status" in tool_names
        assert "rollback_report" in tool_names

        # 4. Create snapshot tool call
        (Path(tmpdir) / "test.txt").write_text("hello mcp")
        snap_req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "rollback_snapshot",
                "arguments": {"label": "mcp_checkpoint_1", "tag": "mcp_stable"},
            },
        }
        snap_resp = server.handle_request(snap_req)
        assert snap_resp is not None
        snap_data = json.loads(snap_resp["result"]["content"][0]["text"])
        assert snap_data["status"] == "success"
        assert snap_data["tag"] == "mcp_stable"
        cp_id = snap_data["checkpoint_id"]

        # 5. Security check tool call
        sec_req = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "rollback_security_check",
                "arguments": {"path": "../../../etc/passwd"},
            },
        }
        sec_resp = server.handle_request(sec_req)
        assert sec_resp is not None
        sec_data = json.loads(sec_resp["result"]["content"][0]["text"])
        assert sec_data["safe"] is False
        assert "traversal" in sec_data["violation_reason"].lower()

        # 6. Revert plan tool call
        plan_req = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "rollback_plan",
                "arguments": {"identifier": cp_id},
            },
        }
        plan_resp = server.handle_request(plan_req)
        assert plan_resp is not None
        assert "result" in plan_resp
        plan_data = json.loads(plan_resp["result"]["content"][0]["text"])
        assert plan_data["status"] == "success"
        assert plan_data["target_checkpoint_id"] == cp_id

        # 7. Revert tool call
        (Path(tmpdir) / "test.txt").write_text("corrupted content")
        revert_req = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "rollback_revert",
                "arguments": {"identifier": cp_id, "dry_run": False},
            },
        }
        revert_resp = server.handle_request(revert_req)
        assert revert_resp is not None
        revert_data = json.loads(revert_resp["result"]["content"][0]["text"])
        assert revert_data["status"] == "success"
        assert (Path(tmpdir) / "test.txt").read_text() == "hello mcp"
