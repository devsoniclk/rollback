from __future__ import annotations

import json
import sys
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
        assert init_resp["result"]["serverInfo"]["version"] == "0.8.0"

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
        assert "rollback_merge" in tool_names
        assert "rollback_patch" in tool_names
        assert "rollback_git_sync" in tool_names
        assert "rollback_revert_file" in tool_names
        assert "rollback_check_integrity" in tool_names
        assert "rollback_scan_drift" in tool_names
        assert "rollback_auto_heal" in tool_names
        assert "rollback_speculative_sandbox" in tool_names
        assert "rollback_acquire_lease" in tool_names
        assert "rollback_release_lease" in tool_names
        assert "rollback_assert_invariants" in tool_names
        assert "rollback_create_shadow" in tool_names
        assert "rollback_evaluate_shadow" in tool_names
        assert "rollback_promote_shadow" in tool_names
        assert "rollback_timeline_graph" in tool_names
        assert "rollback_cas_stats" in tool_names
        assert "rollback_attest_checkpoint" in tool_names
        assert "rollback_consensus_guard_begin" in tool_names
        assert "rollback_consensus_guard_commit" in tool_names
        assert "rollback_consensus_guard_abort" in tool_names
        assert "rollback_occ_transaction" in tool_names
        assert len(tool_names) == 29

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

        # 8. Git sync status tool call
        git_req = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "rollback_git_sync",
                "arguments": {"action": "status"},
            },
        }
        git_resp = server.handle_request(git_req)
        assert git_resp is not None
        git_data = json.loads(git_resp["result"]["content"][0]["text"])
        assert "is_git_repo" in git_data

        # 9. Patch export tool call
        patch_req = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "rollback_patch",
                "arguments": {"action": "export", "from_checkpoint": cp_id},
            },
        }
        patch_resp = server.handle_request(patch_req)
        assert patch_resp is not None
        patch_data = json.loads(patch_resp["result"]["content"][0]["text"])
        assert patch_data["status"] == "success"
        assert patch_data["action"] == "export"

        # 10. Drift tool call
        drift_req = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "rollback_scan_drift",
                "arguments": {},
            },
        }
        drift_resp = server.handle_request(drift_req)
        assert drift_resp is not None
        drift_data = json.loads(drift_resp["result"]["content"][0]["text"])
        assert "is_dirty" in drift_data

        # 11. Integrity tool call
        integ_req = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "rollback_check_integrity",
                "arguments": {},
            },
        }
        integ_resp = server.handle_request(integ_req)
        assert integ_resp is not None
        integ_data = json.loads(integ_resp["result"]["content"][0]["text"])
        assert "is_clean" in integ_data

        # 12. Selective revert tool call
        (Path(tmpdir) / "test.txt").write_text("another mutation")
        sel_req = {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "rollback_revert_file",
                "arguments": {"checkpoint_id": cp_id, "paths": ["test.txt"], "dry_run": False},
            },
        }
        sel_resp = server.handle_request(sel_req)
        assert sel_resp is not None
        sel_data = json.loads(sel_resp["result"]["content"][0]["text"])
        assert sel_data["status"] == "success"
        assert (Path(tmpdir) / "test.txt").read_text() == "hello mcp"

        # 13. Auto-Heal tool call
        heal_req = {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "rollback_auto_heal",
                "arguments": {
                    "command": f'"{sys.executable}" -c "import sys; sys.exit(0)"',
                    "auto_revert": True,
                },
            },
        }
        heal_resp = server.handle_request(heal_req)
        assert heal_resp is not None
        heal_data = json.loads(heal_resp["result"]["content"][0]["text"])
        assert heal_data["success"] is True

        # 14. Speculative sandbox tool call
        sb_req = {
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {
                "name": "rollback_speculative_sandbox",
                "arguments": {
                    "action": "commit",
                    "writes": {"sandboxed.py": "x = 42\n"},
                },
            },
        }
        sb_resp = server.handle_request(sb_req)
        assert sb_resp is not None
        sb_data = json.loads(sb_resp["result"]["content"][0]["text"])
        assert sb_data["status"] == "success"
        assert (Path(tmpdir) / "sandboxed.py").exists()

        # 15. Lease acquire and release tool calls
        lease_req = {
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {
                "name": "rollback_acquire_lease",
                "arguments": {
                    "agent_id": "test-agent",
                    "paths": ["sandboxed.py"],
                    "mode": "exclusive",
                    "ttl_seconds": 60,
                },
            },
        }
        lease_resp = server.handle_request(lease_req)
        assert lease_resp is not None
        lease_data = json.loads(lease_resp["result"]["content"][0]["text"])
        assert lease_data["status"] == "success"
        token = lease_data["lease"]["token"]

        rel_req = {
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/call",
            "params": {
                "name": "rollback_release_lease",
                "arguments": {"token": token},
            },
        }
        rel_resp = server.handle_request(rel_req)
        assert rel_resp is not None
        rel_data = json.loads(rel_resp["result"]["content"][0]["text"])
        assert rel_data["status"] == "success"
        assert rel_data["released"] is True

        # 17. Invariant check tool call
        inv_req = {
            "jsonrpc": "2.0",
            "id": 17,
            "method": "tools/call",
            "params": {
                "name": "rollback_assert_invariants",
                "arguments": {"strict": False},
            },
        }
        inv_resp = server.handle_request(inv_req)
        assert inv_resp is not None
        inv_data = json.loads(inv_resp["result"]["content"][0]["text"])
        assert "passed" in inv_data

        # 18. Shadow clone tool calls
        sh_create_req = {
            "jsonrpc": "2.0",
            "id": 18,
            "method": "tools/call",
            "params": {
                "name": "rollback_create_shadow",
                "arguments": {"hypothesis_id": "hyp-mcp-1", "description": "MCP shadow test"},
            },
        }
        sh_create_resp = server.handle_request(sh_create_req)
        assert sh_create_resp is not None
        sh_create_data = json.loads(sh_create_resp["result"]["content"][0]["text"])
        assert sh_create_data["status"] == "success"

        sh_eval_req = {
            "jsonrpc": "2.0",
            "id": 19,
            "method": "tools/call",
            "params": {
                "name": "rollback_evaluate_shadow",
                "arguments": {
                    "hypothesis_id": "hyp-mcp-1",
                    "test_cmd": "python3 -c 'exit(0)'",
                },
            },
        }
        sh_eval_resp = server.handle_request(sh_eval_req)
        assert sh_eval_resp is not None
        sh_eval_data = json.loads(sh_eval_resp["result"]["content"][0]["text"])
        assert sh_eval_data["result"]["passed"] is True

        sh_prom_req = {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "rollback_promote_shadow",
                "arguments": {"hypothesis_id": "hyp-mcp-1"},
            },
        }
        sh_prom_resp = server.handle_request(sh_prom_req)
        assert sh_prom_resp is not None
        sh_prom_data = json.loads(sh_prom_resp["result"]["content"][0]["text"])
        assert sh_prom_data["status"] == "promoted"

        # 19. Timeline graph tool call
        tl_req = {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "rollback_timeline_graph",
                "arguments": {"as_json": True},
            },
        }
        tl_resp = server.handle_request(tl_req)
        assert tl_resp is not None
        tl_data = json.loads(tl_resp["result"]["content"][0]["text"])
        assert "total_checkpoints" in tl_data

        # 20. CAS stats tool call
        cas_req = {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "rollback_cas_stats",
                "arguments": {},
            },
        }
        cas_resp = server.handle_request(cas_req)
        assert cas_resp is not None
        cas_data = json.loads(cas_resp["result"]["content"][0]["text"])
        assert cas_data["status"] == "success"
        assert "cas_stats" in cas_data
