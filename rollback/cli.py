import argparse
import sys
from pathlib import Path
from .core import RevertPlan, RollbackSession
from .diff import compute_diff


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rollback",
        description="Transactional filesystem & state rollback engine for autonomous AI coding agents.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Init
    p_init = subparsers.add_parser("init", help="Initialize rollback journal in current workspace.")
    p_init.add_argument("--root", default=".", help="Root directory path.")

    # Snapshot
    p_snap = subparsers.add_parser("snapshot", help="Create an immutable checkpoint.")
    p_snap.add_argument("label", nargs="?", default="manual_snapshot", help="Label for this checkpoint.")
    p_snap.add_argument("--tag", default=None, help="Optional tag alias to assign.")
    p_snap.add_argument("--root", default=".", help="Root directory path.")

    # Tag
    p_tag = subparsers.add_parser("tag", help="Assign a named tag alias to a checkpoint.")
    p_tag.add_argument("checkpoint_id", help="Checkpoint ID or 'HEAD'.")
    p_tag.add_argument("tag_name", help="Tag name (e.g., 'v1.0', 'stable').")
    p_tag.add_argument("--root", default=".", help="Root directory path.")

    # Revert
    p_revert = subparsers.add_parser("revert", help="Revert to a checkpoint ID, tag, or last step.")
    p_revert.add_argument("identifier", nargs="?", default=None, help="Target checkpoint ID or tag. If omitted, reverts 1 step.")
    p_revert.add_argument("--dry-run", action="store_true", help="Preview revert operations without modifying disk.")
    p_revert.add_argument("--root", default=".", help="Root directory path.")

    # Plan
    p_plan = subparsers.add_parser("plan", help="Preview operations needed to revert to target state.")
    p_plan.add_argument("identifier", nargs="?", default="HEAD", help="Target checkpoint ID or tag.")
    p_plan.add_argument("--root", default=".", help="Root directory path.")

    # Branch
    p_branch = subparsers.add_parser("branch", help="Manage timeline branches for parallel exploration.")
    p_branch.add_argument("subaction", choices=["list", "create"], default="list", nargs="?", help="Subaction: list or create.")
    p_branch.add_argument("name", nargs="?", default=None, help="Branch name when creating.")
    p_branch.add_argument("--from-cp", default=None, help="Source checkpoint (defaults to HEAD).")
    p_branch.add_argument("--root", default=".", help="Root directory path.")

    # Checkout
    p_checkout = subparsers.add_parser("checkout", help="Switch workspace to a branch or checkpoint.")
    p_checkout.add_argument("target", help="Branch name or checkpoint ID/tag.")
    p_checkout.add_argument("--root", default=".", help="Root directory path.")

    # Bundle
    p_bundle = subparsers.add_parser("bundle", help="Export or import portable session archives (.rbk.tar.gz).")
    p_bundle.add_argument("action", choices=["export", "import"], help="Export or import bundle.")
    p_bundle.add_argument("path", help="Archive file path (.rbk.tar.gz).")
    p_bundle.add_argument("--root", default=".", help="Target workspace root directory.")

    # Diff
    p_diff = subparsers.add_parser("diff", help="Show diff against a checkpoint or between two checkpoints.")
    p_diff.add_argument("from_checkpoint", nargs="?", default=None, help="Baseline checkpoint ID or tag. Defaults to HEAD.")
    p_diff.add_argument("to_checkpoint", nargs="?", default=None, help="Target checkpoint ID or tag. If omitted, diffs against live workspace.")
    p_diff.add_argument("--root", default=".", help="Root directory path.")

    # Log
    p_log = subparsers.add_parser("log", help="List checkpoint history and tags.")
    p_log.add_argument("--root", default=".", help="Root directory path.")

    # Clean / GC
    p_clean = subparsers.add_parser("clean", help="Prune old checkpoints and delete unreferenced blobs.")
    p_clean.add_argument("--keep", type=int, default=10, help="Number of recent checkpoints to retain (default: 10).")
    p_clean.add_argument("--all", action="store_true", help="Purge all checkpoints and blobs completely.")
    p_clean.add_argument("--root", default=".", help="Root directory path.")

    # Report / HTML Dossier
    p_rep = subparsers.add_parser("report", help="Generate an interactive HTML session dossier visualizer.")
    p_rep.add_argument("--html", default="rollback_dossier.html", help="Output file path (default: rollback_dossier.html).")
    p_rep.add_argument("--title", default="Rollback Session Dossier", help="Dossier title.")
    p_rep.add_argument("--root", default=".", help="Root directory path.")

    # Replay
    p_rpl = subparsers.add_parser("replay", help="Replay history deterministically to a target checkpoint or tag.")
    p_rpl.add_argument("target", help="Target checkpoint ID, tag name, or 'HEAD'.")
    p_rpl.add_argument("--step", action="store_true", help="Step through intermediate checkpoints sequentially.")
    p_rpl.add_argument("--dry-run", action="store_true", help="Simulate replay operations without modifying disk.")
    p_rpl.add_argument("--root", default=".", help="Root directory path.")

    # MCP Server
    p_mcp = subparsers.add_parser("mcp", help="Start the Model Context Protocol (MCP) stdio tool server for AI coding agents.")
    p_mcp.add_argument("--root", default=".", help="Root directory path.")

    # Merge
    p_merge = subparsers.add_parser("merge", help="Merge another branch into current branch with 3-way DAG analysis.")
    p_merge.add_argument("source_branch", help="Source branch to merge into current branch.")
    p_merge.add_argument("--into", default=None, help="Target branch (defaults to current branch).")
    p_merge.add_argument("--abort-on-conflict", action="store_true", help="Abort and revert workspace if conflicts occur.")
    p_merge.add_argument("--root", default=".", help="Root directory path.")

    # Patch Export
    p_patch = subparsers.add_parser("patch", help="Export unified diff patch between checkpoints.")
    p_patch.add_argument("from_checkpoint", help="Baseline checkpoint ID, tag, or 'HEAD'.")
    p_patch.add_argument("to_checkpoint", nargs="?", default=None, help="Target checkpoint ID or live workspace.")
    p_patch.add_argument("-o", "--output", default=None, help="Output file path (prints to stdout if omitted).")
    p_patch.add_argument("--root", default=".", help="Root directory path.")

    # Apply Patch
    p_app_patch = subparsers.add_parser("apply-patch", help="Apply a unified diff patch to live workspace.")
    p_app_patch.add_argument("patch_file", help="Path to unified diff patch file.")
    p_app_patch.add_argument("--dry-run", action="store_true", help="Simulate patch application without modifying disk.")
    p_app_patch.add_argument("--root", default=".", help="Root directory path.")

    # Git Sync
    p_git = subparsers.add_parser("git-sync", help="Synchronize rollback checkpoints with local Git commits.")
    p_git.add_argument("action", choices=["status", "export", "import"], help="Git bridge action.")
    p_git.add_argument("--branch", default=None, help="Rollback branch to export.")
    p_git.add_argument("--git-branch", default=None, help="Git branch to export onto.")
    p_git.add_argument("--max-commits", type=int, default=10, help="Max Git commits to import.")
    p_git.add_argument("--root", default=".", help="Root directory path.")

    # Revert-File (Selective)
    p_rev_file = subparsers.add_parser("revert-file", help="Selectively restore specific files from a prior checkpoint.")
    p_rev_file.add_argument("checkpoint_id", help="Checkpoint ID or tag to restore files from.")
    p_rev_file.add_argument("files", nargs="+", help="One or more file paths to restore.")
    p_rev_file.add_argument("--dry-run", action="store_true", help="Preview selective revert without mutating disk.")
    p_rev_file.add_argument("--root", default=".", help="Root directory path.")

    # Integrity Gate
    p_integ = subparsers.add_parser("integrity", help="Audit code syntax and structural integrity across workspace.")
    p_integ.add_argument("--path", default=None, help="Specific file or directory to audit (defaults to workspace).")
    p_integ.add_argument("--strict", action="store_true", help="Exit with error if any syntax issues are found.")
    p_integ.add_argument("--json", action="store_true", help="Output audit report in JSON format.")
    p_integ.add_argument("--root", default=".", help="Root directory path.")

    # Drift / Sentinel
    p_drift = subparsers.add_parser("drift", help="Scan uncommitted workspace drift against HEAD checkpoint.")
    p_drift.add_argument("--auto-checkpoint", action="store_true", help="Trigger micro-checkpoint if drift is detected.")
    p_drift.add_argument("--tag", default=None, help="Optional tag alias if auto-checkpointing.")
    p_drift.add_argument("--json", action="store_true", help="Output drift report in JSON format.")
    p_drift.add_argument("--root", default=".", help="Root directory path.")


    args = parser.parse_args()
    session = RollbackSession(root_dir=args.root)

    if args.command == "init":
        print(f"Initialized rollback journal at {session.journal_dir}")

    elif args.command == "snapshot":
        cp = session.checkpoint(label=args.label)
        if args.tag:
            session.tag(cp.id, args.tag)
            print(f"Created checkpoint: {cp.id} [tag: {args.tag}] (label: '{cp.label}', tracking {len(cp.files)} files)")
        else:
            print(f"Created checkpoint: {cp.id} (label: '{cp.label}', tracking {len(cp.files)} files)")

    elif args.command == "tag":
        try:
            session.tag(args.checkpoint_id, args.tag_name)
            target = session.get_checkpoint(args.checkpoint_id)
            print(f"Tagged checkpoint {target.id if target else args.checkpoint_id} as '{args.tag_name}'.")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif args.command == "plan":
        try:
            plan = session.plan_revert(args.identifier)
            print(f"=== Revert Plan to {plan.target_checkpoint_id} ('{plan.target_label}') ===")
            print(f"Total changes: {plan.total_changes} ({plan.total_bytes_affected} bytes affected)")
            for r in plan.files_to_restore:
                print(f"  [RESTORE]   {r}")
            for d in plan.files_to_delete:
                print(f"  [DELETE]    {d}")
            for u in plan.files_to_resurrect:
                print(f"  [RESURRECT] {u}")
        except Exception as e:
            print(f"Error generating plan: {e}")
            sys.exit(1)

    elif args.command == "revert":
        if not session.history:
            print("No checkpoints found to revert to.")
            sys.exit(1)
        target = args.identifier or session.history[-1]
        try:
            if args.dry_run:
                plan = session.revert(target, dry_run=True)
                assert isinstance(plan, RevertPlan)
                print(f"Dry-run plan for {target}: {plan.total_changes} changes ({plan.total_bytes_affected} bytes affected).")
                for r in plan.files_to_restore:
                    print(f"  [WILL RESTORE]   {r}")
                for d in plan.files_to_delete:
                    print(f"  [WILL DELETE]    {d}")
                for u in plan.files_to_resurrect:
                    print(f"  [WILL RESURRECT] {u}")
            else:
                actions = session.revert(target)
                print(f"Reverted to {target}: {len(actions)} files modified/restored.")
                for path, act in actions.items():
                    print(f"  [{act.upper()}] {path}")
        except Exception as e:
            print(f"Error reverting: {e}")
            sys.exit(1)

    elif args.command == "branch":
        if args.subaction == "list" or not args.name:
            b_info = session.list_branches()
            print(f"Current branch: * {b_info['current']}")
            for b_name, b_cid in b_info["branches"].items():
                mark = "*" if b_name == b_info["current"] else " "
                print(f"  {mark} {b_name} -> {b_cid}")
        else:
            cid = session.create_branch(args.name, from_checkpoint=args.from_cp)
            print(f"Created branch '{args.name}' pointing to {cid}.")

    elif args.command == "checkout":
        try:
            actions = session.checkout(args.target)
            print(f"Switched to '{args.target}' ({len(actions)} file operations).")
        except Exception as e:
            print(f"Error checking out '{args.target}': {e}")
            sys.exit(1)

    elif args.command == "bundle":
        if args.action == "export":
            try:
                out = session.export_bundle(args.path)
                print(f"Exported session bundle to {out}")
            except Exception as e:
                print(f"Error exporting bundle: {e}")
                sys.exit(1)
        elif args.action == "import":
            try:
                s = RollbackSession.import_bundle(args.path, target_root=args.root)
                print(f"Imported session bundle into {s.root} ({len(s.history)} checkpoints loaded).")
            except Exception as e:
                print(f"Error importing bundle: {e}")
                sys.exit(1)

    elif args.command == "diff":
        if not session.history:
            print("No checkpoints found.")
            sys.exit(1)
        from_id = args.from_checkpoint or "HEAD"
        try:
            diff_res = compute_diff(session, from_checkpoint=from_id, to_checkpoint=args.to_checkpoint)
            print(diff_res.format_diff())
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif args.command == "log":
        if not session.history:
            print("No checkpoints in history.")
            return
        tag_reverse_map = {}
        for tname, cid in session.tags.items():
            tag_reverse_map.setdefault(cid, []).append(tname)

        print(f"History ({len(session.history)} checkpoints):")
        for cid in reversed(session.history):
            cp = session.get_checkpoint(cid)
            if cp:
                tags_str = f" [tags: {', '.join(tag_reverse_map[cid])}]" if cid in tag_reverse_map else ""
                branch_str = f" ({cp.branch})" if cp.branch else ""
                print(f"  {cp.id}{tags_str}{branch_str} | {cp.label} | {len(cp.files)} files")

    elif args.command == "clean":
        res = session.clean(keep_last=args.keep, purge_all=args.all)
        print(
            f"Cleaned journal: {res['checkpoints_removed']} checkpoints removed, "
            f"{res['blobs_removed']} unreferenced blobs deleted, {res['bytes_reclaimed']} bytes reclaimed."
        )

    elif args.command == "report":
        from .html_report import render_html_report
        out_p = Path(args.html)
        render_html_report(session, output_path=out_p, title=args.title)
        print(f"Generated visual session dossier at {out_p.resolve()}")

    elif args.command == "replay":
        try:
            res = session.replay(
                target_identifier=args.target,
                step_by_step=args.step,
                dry_run=args.dry_run,
                on_step=lambda st: print(f"  [STEP {st.step_number}] -> {st.to_checkpoint_id[:8]} ({st.label}) in {st.duration_ms:.1f}ms"),
            )
            print(res.message)
        except Exception as e:
            print(f"Error during replay: {e}")
            sys.exit(1)

    elif args.command == "mcp":
        from .mcp_server import RollbackMCPServer
        server = RollbackMCPServer(root_dir=args.root)
        server.run_stdio()

    elif args.command == "merge":
        try:
            res = session.merge_branch(
                args.source_branch,
                target_branch=args.into,
                abort_on_conflict=args.abort_on_conflict,
            )
            print(f"Merge Status: {res.status.upper()}")
            print(f"Message: {res.message}")
            if res.merged_files:
                print(f"Merged files ({len(res.merged_files)}): {', '.join(res.merged_files)}")
            if res.conflicting_files:
                print(f"Conflicts ({len(res.conflicting_files)}): {', '.join(res.conflicting_files)}")
            if res.has_conflicts:
                sys.exit(1)
        except Exception as e:
            print(f"Merge failed: {e}")
            sys.exit(1)

    elif args.command == "patch":
        from .patch import export_patch
        try:
            txt = export_patch(
                session,
                args.from_checkpoint,
                to_checkpoint=args.to_checkpoint,
                output_file=args.output,
            )
            if args.output:
                print(f"Exported patch to {args.output} ({len(txt)} bytes)")
            else:
                print(txt)
        except Exception as e:
            print(f"Patch export failed: {e}")
            sys.exit(1)

    elif args.command == "apply-patch":
        from .patch import apply_patch
        try:
            res = apply_patch(session, args.patch_file, dry_run=args.dry_run)
            if res.status == "failed":
                print(f"Patch apply failed: {res.message}")
                for fh in res.failed_hunks:
                    print(f"  {fh['file']}: {fh['error']}")
                sys.exit(1)
            else:
                print(res.message)
                if res.files_affected:
                    print(f"Files affected: {', '.join(res.files_affected)}")
        except Exception as e:
            print(f"Apply patch failed: {e}")
            sys.exit(1)

    elif args.command == "git-sync":
        from .git_bridge import GitBridge
        bridge = GitBridge(session)
        if args.action == "status":
            st = bridge.get_status()
            print("Git Bridge Status:")
            print(f"  Is Git Repo: {st.is_git_repo}")
            print(f"  Git Branch: {st.git_branch} (HEAD: {st.git_head[:8] if st.git_head else 'none'})")
            print(f"  Rollback Branch: {st.rollback_branch} (HEAD: {st.rollback_head[:8] if st.rollback_head else 'none'})")
            print(f"  Dirty Files: {len(st.dirty_files)}")
        elif args.action == "export":
            res = bridge.export_to_git(branch=args.branch, git_branch=args.git_branch)
            print(res["message"])
        elif args.action == "import":
            cids = bridge.import_from_git(max_commits=args.max_commits)
            print(f"Imported {len(cids)} commit(s) into rollback checkpoints.")

    elif args.command == "revert-file":
        try:
            res = session.revert_files(args.checkpoint_id, args.files, dry_run=args.dry_run)
            if args.dry_run:
                plan = res["plan"]
                print(f"Selective Revert Plan (Dry Run) from {plan['target_checkpoint_id'][:8]}:")
                print(f"  Total changes: {plan['total_changes']}")
                for op in plan["operations"]:
                    print(f"  - {op['path']}: {op['action']}")
            else:
                print(f"Selective Revert successful from {res['target_checkpoint_id'][:8]}!")
                for path, act in res["files_affected"].items():
                    print(f"  {path}: {act}")
        except Exception as e:
            print(f"Selective revert failed: {e}")
            sys.exit(1)

    elif args.command == "integrity":
        import json
        paths = [args.path] if args.path else None
        try:
            report = session.check_integrity(paths=paths, strict=args.strict)
            if args.json:
                print(json.dumps(report.to_dict(), indent=2))
            else:
                status_str = "CLEAN" if report.is_clean else "FAILURES DETECTED"
                print(f"Integrity Audit [{status_str}]:")
                print(f"  Files checked: {report.files_checked}")
                print(f"  Passed: {report.passed_count}")
                print(f"  Failed: {report.failed_count}")
                if report.failures:
                    print("\nIssues:")
                    for f in report.failures:
                        print(f"  - {f.path} ({f.language}): {f.error_message}")
            if args.strict and not report.is_clean:
                sys.exit(1)
        except Exception as e:
            print(f"Integrity check error: {e}")
            sys.exit(1)

    elif args.command == "drift":
        import json
        from .sentinel import WorkspaceSentinel
        sentinel = WorkspaceSentinel(session)
        if args.auto_checkpoint:
            cp = sentinel.auto_checkpoint(tag=args.tag)
            if cp:
                print(f"Created auto-checkpoint {cp.id[:8]} ({cp.label})")
            else:
                print("Workspace is clean. No checkpoint created.")
        else:
            rep = sentinel.scan_drift()
            if args.json:
                print(json.dumps(rep.to_dict(), indent=2))
            else:
                status_str = "DIRTY" if rep.is_dirty else "CLEAN"
                print(f"Workspace Sentinel Drift [{status_str}]:")
                print(f"  HEAD: {rep.head_checkpoint_id[:8] if rep.head_checkpoint_id else 'none'} ({rep.head_label})")
                print(f"  Total Changes: {rep.total_changes}")
                print(f"  Modified: {len(rep.modified_files)}")
                print(f"  Untracked: {len(rep.untracked_files)}")
                print(f"  Deleted: {len(rep.deleted_files)}")
                print(f"  Drift Ratio: {rep.drift_ratio:.1%}")
                if rep.files:
                    print("\nChanged Files:")
                    for f in rep.files:
                        print(f"  - [{f.status.upper()}] {f.path} (delta {f.size_delta:+d} bytes)")



if __name__ == "__main__":
    main()
