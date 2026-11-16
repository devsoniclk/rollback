from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import RevertPlan, RollbackSession
from .diff import compute_diff
from .html_report import render_html_report
from .security import SecurityPolicy, SecurityViolationError


class RollbackMCPServer:
    """
    Standard-compliant Model Context Protocol (MCP) stdio server for rollback.
    Provides tool primitives for AI coding agents (Claude Code, OpenBot, Cursor, Cline).
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, root_dir: str = "."):
        self.root_dir = root_dir
        self.session = RollbackSession(root_dir=root_dir)

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "rollback_snapshot",
                "description": "Create an immutable transactional micro-checkpoint of the workspace before running an agent tool.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Descriptive label for this checkpoint (e.g., 'pre_refactor_auth').",
                            "default": "agent_snapshot",
                        },
                        "tag": {
                            "type": "string",
                            "description": "Optional named tag alias (e.g., 'v1.0', 'stable').",
                        },
                    },
                },
            },
            {
                "name": "rollback_revert",
                "description": "Atomically revert workspace files to a target checkpoint ID, tag, or 1 step back.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "identifier": {
                            "type": "string",
                            "description": "Checkpoint ID or tag name. If omitted, reverts 1 step back.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "If true, simulates operations without writing to disk.",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "rollback_plan",
                "description": "Preview files that would be restored, deleted, or resurrected when reverting to a target state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "identifier": {
                            "type": "string",
                            "description": "Target checkpoint ID, tag, or 'HEAD'.",
                            "default": "HEAD",
                        },
                    },
                },
            },
            {
                "name": "rollback_diff",
                "description": "Inspect additions, modifications, and deletions between checkpoints or against live workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "from_checkpoint": {
                            "type": "string",
                            "description": "Baseline checkpoint ID or tag. Defaults to HEAD.",
                        },
                        "to_checkpoint": {
                            "type": "string",
                            "description": "Target checkpoint ID or tag. If omitted, diffs against live workspace.",
                        },
                    },
                },
            },
            {
                "name": "rollback_branch",
                "description": "Create or checkout hypothesis branches for exploring parallel agent solution branches.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "checkout", "list"],
                            "description": "Branch operation to perform.",
                            "default": "list",
                        },
                        "name": {
                            "type": "string",
                            "description": "Branch name when creating or checking out.",
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "rollback_security_check",
                "description": "Validate whether a target file path is contained within workspace root and satisfies secret policies.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to validate.",
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "rollback_status",
                "description": "Inspect active branch, recent checkpoints, journal storage size, and workspace containment status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "rollback_report",
                "description": "Generate an interactive, dark-mode HTML session dossier visualizer.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "output_file": {
                            "type": "string",
                            "description": "Path to write the standalone HTML report (e.g. 'report.html').",
                            "default": "rollback_dossier.html",
                        },
                    },
                },
            },
        ]

    def execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "rollback_snapshot":
            label = args.get("label", "agent_snapshot")
            tag = args.get("tag")
            cp = self.session.checkpoint(label=label)
            if tag:
                self.session.tag(cp.id, tag)
            return json.dumps({
                "status": "success",
                "checkpoint_id": cp.id,
                "label": cp.label,
                "branch": cp.branch,
                "files_count": len(cp.files),
                "timestamp": cp.timestamp,
                "tag": tag,
            }, indent=2)

        elif name == "rollback_revert":
            identifier = args.get("identifier")
            dry_run = bool(args.get("dry_run", False))
            reverted = self.session.revert(checkpoint_id=identifier, dry_run=dry_run)
            return json.dumps({
                "status": "success",
                "mode": "dry_run" if dry_run else "executed",
                "files_affected_count": len(reverted),
                "files": reverted,
            }, indent=2)

        elif name == "rollback_plan":
            identifier = args.get("identifier", "HEAD")
            plan = self.session.plan_revert(identifier)
            return json.dumps({
                "status": "success",
                "target_checkpoint_id": plan.target_checkpoint_id,
                "target_label": plan.target_label,
                "files_to_restore": plan.files_to_restore,
                "files_to_delete": plan.files_to_delete,
                "files_to_resurrect": plan.files_to_resurrect,
                "total_bytes_affected": plan.total_bytes_affected,
                "total_changes": plan.total_changes,
            }, indent=2)

        elif name == "rollback_diff":
            from_cp = args.get("from_checkpoint")
            to_cp = args.get("to_checkpoint")
            diff = compute_diff(self.session, from_checkpoint=from_cp, to_checkpoint=to_cp)
            return json.dumps({
                "status": "success",
                "is_clean": diff.is_clean,
                "added": diff.added,
                "modified": diff.modified,
                "deleted": diff.deleted,
            }, indent=2)

        elif name == "rollback_branch":
            action = args.get("action", "list")
            b_name = args.get("name")
            if action == "list":
                branches = self.session.list_branches()
                return json.dumps({
                    "status": "success",
                    "current_branch": self.session.current_branch,
                    "branches": branches,
                }, indent=2)
            elif action == "create":
                if not b_name:
                    raise ValueError("Branch name required for 'create' action.")
                b_id = self.session.create_branch(b_name)
                return json.dumps({
                    "status": "success",
                    "created_branch": b_name,
                    "head_checkpoint_id": b_id,
                }, indent=2)
            elif action == "checkout":
                if not b_name:
                    raise ValueError("Branch name required for 'checkout' action.")
                self.session.checkout(b_name)
                return json.dumps({
                    "status": "success",
                    "checked_out": b_name,
                    "current_branch": self.session.current_branch,
                }, indent=2)
            else:
                raise ValueError(f"Unknown branch action: {action}")

        elif name == "rollback_security_check":
            path_str = args.get("path", "")
            policy = self.session.security_policy or SecurityPolicy(allowed_root=self.session.root)
            try:
                policy.validate_target_path(path_str)
                is_safe = True
                reason = None
            except SecurityViolationError as e:
                is_safe = False
                reason = str(e)
            return json.dumps({
                "status": "success",
                "path": path_str,
                "safe": is_safe,
                "violation_reason": reason,
            }, indent=2)

        elif name == "rollback_status":
            head_id = self.session.history[-1] if self.session.history else None
            return json.dumps({
                "status": "success",
                "root": str(self.session.root),
                "current_branch": self.session.current_branch,
                "checkpoints_count": len(self.session.history),
                "head_checkpoint": head_id,
                "branches": list(self.session.branches.keys()),
                "tags": self.session.tags,
            }, indent=2)

        elif name == "rollback_report":
            out_file = args.get("output_file", "rollback_dossier.html")
            render_html_report(self.session, output_path=out_file)
            return json.dumps({
                "status": "success",
                "report_file": str(out_file),
                "message": f"Interactive HTML dossier generated at {out_file}",
            }, indent=2)

        raise ValueError(f"Unknown tool: {name}")

    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = req.get("method")
        msg_id = req.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "serverInfo": {
                        "name": "rollback-engine",
                        "version": "0.4.0",
                    },
                    "capabilities": {
                        "tools": {},
                    },
                },
            }

        elif method == "notifications/initialized":
            return None

        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {},
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": self.get_tool_definitions(),
                },
            }

        elif method == "tools/call":
            params = req.get("params", {})
            t_name = params.get("name", "")
            t_args = params.get("arguments", {})
            try:
                result_text = self.execute_tool(t_name, t_args)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": result_text,
                            }
                        ],
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": str(e),
                    },
                }

        else:
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found",
                    },
                }
            return None

    def run_stdio(self) -> None:
        """Runs the JSON-RPC stdio event loop."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


def main() -> None:
    server = RollbackMCPServer()
    server.run_stdio()


if __name__ == "__main__":
    main()
