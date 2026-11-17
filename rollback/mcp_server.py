from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import RevertPlan, RollbackSession
from .diff import compute_diff
from .git_bridge import GitBridge
from .html_report import render_html_report
from .merge import MergeConflictError, MergeEngine
from .patch import apply_patch, export_patch
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
            {
                "name": "rollback_merge",
                "description": "Execute 3-way branch merge with automatic common ancestor detection and conflict markers.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source_branch": {
                            "type": "string",
                            "description": "Source branch to merge into current active branch.",
                        },
                        "target_branch": {
                            "type": "string",
                            "description": "Optional target branch (defaults to current active branch).",
                        },
                        "abort_on_conflict": {
                            "type": "boolean",
                            "description": "If true, aborts and reverts if conflicts occur instead of writing conflict markers.",
                            "default": False,
                        },
                    },
                    "required": ["source_branch"],
                },
            },
            {
                "name": "rollback_patch",
                "description": "Export or apply standard unified diff patch files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["export", "apply"],
                            "description": "Whether to export or apply a unified patch.",
                        },
                        "from_checkpoint": {
                            "type": "string",
                            "description": "Baseline checkpoint ID/tag when exporting patch.",
                        },
                        "to_checkpoint": {
                            "type": "string",
                            "description": "Target checkpoint ID/tag when exporting patch (omitted for live workspace).",
                        },
                        "patch_content": {
                            "type": "string",
                            "description": "Raw unified diff patch text to apply.",
                        },
                        "patch_file": {
                            "type": "string",
                            "description": "Patch file path for export target or apply source.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Simulate patch application without modifying disk.",
                            "default": False,
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "rollback_git_sync",
                "description": "Synchronize rollback micro-checkpoints with local Git commits and inspect repository sync status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["status", "export", "import"],
                            "description": "Git bridge action to execute.",
                        },
                        "branch": {
                            "type": "string",
                            "description": "Rollback branch to export.",
                        },
                        "git_branch": {
                            "type": "string",
                            "description": "Git branch to export onto.",
                        },
                        "max_commits": {
                            "type": "integer",
                            "description": "Maximum number of recent Git commits to import.",
                            "default": 10,
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "rollback_revert_file",
                "description": "Selectively revert specific files to their state at a target checkpoint without rolling back the whole workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "checkpoint_id": {
                            "type": "string",
                            "description": "Target checkpoint ID or tag alias.",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of file paths to selectively revert.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Simulate selective revert without modifying disk.",
                            "default": False,
                        },
                    },
                    "required": ["checkpoint_id", "paths"],
                },
            },
            {
                "name": "rollback_check_integrity",
                "description": "Audit Python, JSON, YAML, TOML, and JS/TS code syntax and structural integrity across workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional specific file paths to audit (defaults to entire workspace).",
                        },
                        "strict": {
                            "type": "boolean",
                            "description": "Fail immediately on any syntax errors.",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "rollback_scan_drift",
                "description": "Scan uncommitted workspace file drift, modified bytes, and dirty status against HEAD checkpoint.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional file path filter.",
                        },
                    },
                },
            },
            {
                "name": "rollback_auto_heal",
                "description": "Execute command or test suite under auto-heal watchdog; automatically rolls back workspace to green state on failure and generates structured diagnosis.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command line to execute under watchdog guard.",
                        },
                        "auto_revert": {
                            "type": "boolean",
                            "description": "Whether to revert to pre-checkpoint automatically if command fails.",
                            "default": True,
                        },
                        "timeout_seconds": {
                            "type": "number",
                            "description": "Maximum execution time before timeout failure.",
                            "default": 120.0,
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "rollback_speculative_sandbox",
                "description": "Stage speculative file writes and deletions in memory overlay, validate AST/syntax integrity, and commit or discard.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["stage", "commit", "discard"],
                            "description": "Action to perform: 'stage' (write/delete in overlay), 'commit' (flush to disk), or 'discard'.",
                        },
                        "writes": {
                            "type": "object",
                            "description": "Dictionary of relative paths to text contents to stage or commit.",
                        },
                        "deletes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of relative paths to stage for deletion.",
                        },
                        "validate_first": {
                            "type": "boolean",
                            "description": "Whether to enforce AST/syntax integrity validation before committing.",
                            "default": True,
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "rollback_acquire_lease",
                "description": "Acquire an exclusive (write) or shared (read) path lease with TTL for multi-agent swarm concurrency control.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Unique identifier of the requesting AI agent.",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Workspace paths to lock.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["exclusive", "shared"],
                            "description": "Locking mode: 'exclusive' (write lock) or 'shared' (read lock).",
                            "default": "exclusive",
                        },
                        "ttl_seconds": {
                            "type": "number",
                            "description": "Time to live in seconds before lease automatically expires.",
                            "default": 300.0,
                        },
                    },
                    "required": ["agent_id", "paths"],
                },
            },
            {
                "name": "rollback_release_lease",
                "description": "Release an active multi-agent path lease using its secret token.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "token": {
                            "type": "string",
                            "description": "Secret token returned when acquiring the lease.",
                        },
                    },
                    "required": ["token"],
                },
            },
            {
                "name": "rollback_assert_invariants",
                "description": "Evaluate declared invariant assertion rules (tests, syntax, regex constraints, file boundaries) across the workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "strict": {
                            "type": "boolean",
                            "description": "Whether to raise an error if any invariant check fails.",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "rollback_create_shadow",
                "description": "Create an ephemeral copy-on-write shadow clone workspace to test an experimental hypothesis in isolation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "hypothesis_id": {
                            "type": "string",
                            "description": "Unique hypothesis identifier (e.g., 'fix-null-pointer').",
                        },
                        "description": {
                            "type": "string",
                            "description": "Description of the hypothesis being explored.",
                            "default": "",
                        },
                    },
                    "required": ["hypothesis_id"],
                },
            },
            {
                "name": "rollback_evaluate_shadow",
                "description": "Execute a verification test command inside an isolated shadow clone workspace and evaluate results.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "hypothesis_id": {
                            "type": "string",
                            "description": "Hypothesis identifier.",
                        },
                        "test_cmd": {
                            "type": "string",
                            "description": "Shell command to execute inside the shadow clone.",
                        },
                        "timeout_sec": {
                            "type": "number",
                            "description": "Timeout in seconds.",
                            "default": 30.0,
                        },
                    },
                    "required": ["hypothesis_id", "test_cmd"],
                },
            },
            {
                "name": "rollback_promote_shadow",
                "description": "Atomically promote a verified shadow hypothesis into the main workspace and record a checkpoint.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "hypothesis_id": {
                            "type": "string",
                            "description": "Hypothesis identifier.",
                        },
                        "create_checkpoint": {
                            "type": "boolean",
                            "description": "Whether to record an automatic checkpoint on promotion.",
                            "default": True,
                        },
                    },
                    "required": ["hypothesis_id"],
                },
            },
            {
                "name": "rollback_timeline_graph",
                "description": "Generate an ASCII timeline DAG or JSON metadata of checkpoints, tags, branches, and commits.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "branch": {
                            "type": "string",
                            "description": "Filter by branch name.",
                        },
                        "max_nodes": {
                            "type": "integer",
                            "description": "Maximum number of nodes to render.",
                            "default": 20,
                        },
                        "as_json": {
                            "type": "boolean",
                            "description": "Return structured JSON instead of ASCII text.",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "rollback_cas_stats",
                "description": "Query Content-Addressed Storage (CAS) deduplication statistics, compression savings, and stored object counts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
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

        elif name == "rollback_merge":
            src_br = args["source_branch"]
            tgt_br = args.get("target_branch")
            abort_conf = args.get("abort_on_conflict", False)
            res = self.session.merge_branch(
                src_br, target_branch=tgt_br, abort_on_conflict=abort_conf
            )
            return json.dumps(res.to_dict(), indent=2)

        elif name == "rollback_patch":
            action = args["action"]
            if action == "export":
                from_id = args.get("from_checkpoint", "HEAD")
                to_id = args.get("to_checkpoint")
                patch_file = args.get("patch_file")
                patch_text = export_patch(
                    self.session, from_id, to_checkpoint=to_id, output_file=patch_file
                )
                return json.dumps({
                    "status": "success",
                    "action": "export",
                    "from_checkpoint": from_id,
                    "to_checkpoint": to_id,
                    "patch_file": patch_file,
                    "patch_length": len(patch_text),
                    "patch_preview": patch_text[:1000],
                }, indent=2)
            elif action == "apply":
                patch_src = args.get("patch_content") or args.get("patch_file")
                if not patch_src:
                    raise ValueError("Either 'patch_content' or 'patch_file' must be provided.")
                dry_run = args.get("dry_run", False)
                res = apply_patch(self.session, patch_src, dry_run=dry_run)
                return json.dumps(res.to_dict(), indent=2)
            else:
                raise ValueError(f"Unknown patch action: {action}")

        elif name == "rollback_git_sync":
            action = args["action"]
            bridge = GitBridge(self.session)
            if action == "status":
                status = bridge.get_status()
                return json.dumps(status.to_dict(), indent=2)
            elif action == "export":
                res = bridge.export_to_git(
                    branch=args.get("branch"),
                    git_branch=args.get("git_branch"),
                )
                return json.dumps(res, indent=2)
            elif action == "import":
                max_c = args.get("max_commits", 10)
                cids = bridge.import_from_git(max_commits=max_c)
                return json.dumps({
                    "status": "success",
                    "action": "import",
                    "imported_count": len(cids),
                    "imported_checkpoints": cids,
                }, indent=2)
            else:
                raise ValueError(f"Unknown git_sync action: {action}")

        elif name == "rollback_revert_file":
            cid = args.get("checkpoint_id")
            paths = args.get("paths", [])
            dry_run = bool(args.get("dry_run", False))
            if not cid or not paths:
                raise ValueError("'checkpoint_id' and 'paths' are required for rollback_revert_file.")
            res = self.session.revert_files(checkpoint_id=cid, paths=paths, dry_run=dry_run)
            return json.dumps(res, indent=2)

        elif name == "rollback_check_integrity":
            paths = args.get("paths")
            strict = bool(args.get("strict", False))
            report = self.session.check_integrity(paths=paths, strict=strict)
            return json.dumps(report.to_dict(), indent=2)

        elif name == "rollback_scan_drift":
            paths = args.get("paths")
            drift = self.session.scan_drift(paths=paths)
            return json.dumps(drift.to_dict(), indent=2)

        elif name == "rollback_auto_heal":
            command = args.get("command")
            if not command:
                raise ValueError("'command' argument is required for rollback_auto_heal.")
            auto_revert = bool(args.get("auto_revert", True))
            timeout_s = float(args.get("timeout_seconds", 120.0))
            rep = self.session.heal(command, auto_revert=auto_revert, timeout_seconds=timeout_s)
            return json.dumps(rep.to_dict(), indent=2)

        elif name == "rollback_speculative_sandbox":
            action = args.get("action", "commit")
            writes = args.get("writes") or {}
            deletes = args.get("deletes") or []
            validate_first = bool(args.get("validate_first", True))
            sandbox = self.session.speculative_sandbox()
            for path, content in writes.items():
                sandbox.write_file(path, content)
            for path in deletes:
                sandbox.delete_file(path)

            if action == "stage":
                staged = sandbox.list_staged()
                return json.dumps({
                    "status": "success",
                    "action": "stage",
                    "staged_files": staged,
                }, indent=2)
            elif action == "discard":
                sandbox.discard()
                return json.dumps({
                    "status": "success",
                    "action": "discard",
                    "staged_files": {},
                }, indent=2)
            elif action == "commit":
                res = sandbox.commit(create_checkpoint=True, validate_first=validate_first)
                return json.dumps({
                    "status": "success",
                    "action": "commit",
                    **res.to_dict(),
                }, indent=2)
            else:
                raise ValueError(f"Unknown sandbox action: {action}")

        elif name == "rollback_acquire_lease":
            agent_id = args.get("agent_id")
            paths = args.get("paths", [])
            mode = args.get("mode", "exclusive")
            ttl_s = float(args.get("ttl_seconds", 300.0))
            if not agent_id or not paths:
                raise ValueError("'agent_id' and 'paths' are required for rollback_acquire_lease.")
            lease = self.session.acquire_lease(agent_id=agent_id, paths=paths, mode=mode, ttl_seconds=ttl_s)
            return json.dumps({
                "status": "success",
                "lease": lease.to_dict(include_token=True),
            }, indent=2)

        elif name == "rollback_release_lease":
            token = args.get("token")
            if not token:
                raise ValueError("'token' argument is required for rollback_release_lease.")
            released = self.session.release_lease(token)
            return json.dumps({
                "status": "success" if released else "not_found",
                "released": released,
            }, indent=2)

        elif name == "rollback_assert_invariants":
            strict = args.get("strict", False)
            report = self.session.assert_invariants(strict=strict)
            return json.dumps(report.to_dict(), indent=2)

        elif name == "rollback_create_shadow":
            hyp_id = args.get("hypothesis_id")
            if not hyp_id:
                raise ValueError("'hypothesis_id' is required for rollback_create_shadow.")
            desc = args.get("description", "")
            hyp = self.session.shadow_manager.create_hypothesis(hyp_id, description=desc)
            return json.dumps({
                "status": "success",
                "hypothesis": hyp.to_dict(),
            }, indent=2)

        elif name == "rollback_evaluate_shadow":
            hyp_id = args.get("hypothesis_id")
            cmd = args.get("test_cmd")
            if not hyp_id or not cmd:
                raise ValueError("'hypothesis_id' and 'test_cmd' are required for rollback_evaluate_shadow.")
            timeout = float(args.get("timeout_sec", 30.0))
            res = self.session.shadow_manager.evaluate_hypothesis(hyp_id, test_cmd=cmd, timeout_sec=timeout)
            return json.dumps({
                "status": "success",
                "result": res.to_dict(),
            }, indent=2)

        elif name == "rollback_promote_shadow":
            hyp_id = args.get("hypothesis_id")
            if not hyp_id:
                raise ValueError("'hypothesis_id' is required for rollback_promote_shadow.")
            create_cp = args.get("create_checkpoint", True)
            res = self.session.shadow_manager.promote_hypothesis(hyp_id, create_checkpoint=create_cp)
            return json.dumps(res, indent=2)

        elif name == "rollback_timeline_graph":
            branch = args.get("branch")
            max_nodes = int(args.get("max_nodes", 20))
            as_json = args.get("as_json", False)
            if as_json:
                return json.dumps(self.session.timeline.to_dict(branch=branch), indent=2)
            else:
                return self.session.timeline.render_ascii(branch=branch, max_nodes=max_nodes)

        elif name == "rollback_cas_stats":
            stats = self.session.cas_store.get_stats()
            return json.dumps({
                "status": "success",
                "cas_stats": stats.to_dict(),
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
                        "version": "0.8.0",
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
