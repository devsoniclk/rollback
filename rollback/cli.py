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


if __name__ == "__main__":
    main()
