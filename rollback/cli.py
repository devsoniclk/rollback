import argparse
import sys
from pathlib import Path
from .core import RollbackSession
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
    p_revert.add_argument("--root", default=".", help="Root directory path.")

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

    elif args.command == "revert":
        if not session.history:
            print("No checkpoints found to revert to.")
            sys.exit(1)
        target = args.identifier or session.history[-1]
        try:
            actions = session.revert(target)
            print(f"Reverted to {target}: {len(actions)} files modified/restored.")
            for path, act in actions.items():
                print(f"  [{act.upper()}] {path}")
        except Exception as e:
            print(f"Error reverting: {e}")
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
                print(f"  {cp.id}{tags_str} | {cp.label} | {len(cp.files)} files")

    elif args.command == "clean":
        res = session.clean(keep_last=args.keep, purge_all=args.all)
        print(
            f"Cleaned journal: {res['checkpoints_removed']} checkpoints removed, "
            f"{res['blobs_removed']} unreferenced blobs deleted, {res['bytes_reclaimed']} bytes reclaimed."
        )


if __name__ == "__main__":
    main()
