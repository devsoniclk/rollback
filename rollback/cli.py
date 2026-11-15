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
    p_snap.add_argument("--root", default=".", help="Root directory path.")

    # Revert
    p_revert = subparsers.add_parser("revert", help="Revert to a checkpoint ID or last step.")
    p_revert.add_argument("checkpoint_id", nargs="?", default=None, help="Target checkpoint ID. If omitted, reverts 1 step.")
    p_revert.add_argument("--root", default=".", help="Root directory path.")

    # Diff
    p_diff = subparsers.add_parser("diff", help="Show diff against a checkpoint or between checkpoints.")
    p_diff.add_argument("checkpoint_id", nargs="?", default=None, help="Baseline checkpoint ID.")
    p_diff.add_argument("--root", default=".", help="Root directory path.")

    # Log
    p_log = subparsers.add_parser("log", help="List checkpoint history.")
    p_log.add_argument("--root", default=".", help="Root directory path.")

    args = parser.parse_args()
    session = RollbackSession(root_dir=args.root)

    if args.command == "init":
        print(f"Initialized rollback journal at {session.journal_dir}")

    elif args.command == "snapshot":
        cp = session.checkpoint(label=args.label)
        print(f"Created checkpoint: {cp.id} (label: '{cp.label}', tracking {len(cp.files)} files)")

    elif args.command == "revert":
        if not session.history:
            print("No checkpoints found to revert to.")
            sys.exit(1)
        target_cid = args.checkpoint_id or session.history[-1]
        actions = session.revert(target_cid)
        print(f"Reverted to checkpoint {target_cid}: {len(actions)} files modified/restored.")
        for path, act in actions.items():
            print(f"  [{act.upper()}] {path}")

    elif args.command == "diff":
        if not session.history:
            print("No checkpoints found.")
            sys.exit(1)
        cid = args.checkpoint_id or session.history[-1]
        cp = session.get_checkpoint(cid)
        if not cp:
            print(f"Error: checkpoint {cid} not found.")
            sys.exit(1)
        diff_res = compute_diff(session, cp)
        print(diff_res.format_diff())

    elif args.command == "log":
        if not session.history:
            print("No checkpoints in history.")
            return
        print(f"History ({len(session.history)} checkpoints):")
        for cid in reversed(session.history):
            cp = session.get_checkpoint(cid)
            if cp:
                print(f"  {cp.id} | {cp.label} | {len(cp.files)} files")


if __name__ == "__main__":
    main()
