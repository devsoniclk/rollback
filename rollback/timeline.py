"""
rollback.timeline: Interactive Timeline DAG & Visual Tree Visualizer for AI coding agents.

Constructs directed acyclic graphs of checkpoints, branches, tags, and merges,
rendering clean ASCII branch diagrams and structured machine-readable metadata.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .core import RollbackSession


@dataclass
class TimelineNode:
    checkpoint_id: str
    timestamp: float
    iso_time: str
    label: str
    branch: str
    tags: List[str] = field(default_factory=list)
    files_count: int = 0
    is_head: bool = False
    parent_id: Optional[str] = None
    delta_added: int = 0
    delta_removed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TimelineGraph:
    """
    Parses checkpoint history and constructs a navigable timeline DAG.
    """

    def __init__(self, session: RollbackSession):
        self.session = session

    def build_nodes(self, branch: Optional[str] = None) -> List[TimelineNode]:
        checkpoints = self.session.list_checkpoints()
        if not checkpoints:
            return []

        tags = self.session.list_tags()
        # Invert tags map: checkpoint_id -> list of tag names
        cp_tags: Dict[str, List[str]] = {}
        for tag_name, cp_id in tags.items():
            cp_tags.setdefault(cp_id, []).append(tag_name)

        active_branch = self.session.get_current_branch()
        try:
            head_cp = self.session.get_checkpoint("HEAD")
            head_id = head_cp.id if head_cp else None
        except Exception:
            head_id = None

        nodes: List[TimelineNode] = []
        for i, cp in enumerate(checkpoints):
            cp_branch = getattr(cp, "branch", "main") or "main"
            if branch and cp_branch != branch:
                continue

            iso_time = datetime.fromtimestamp(cp.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            parent_id = checkpoints[i - 1].id if i > 0 else None

            # Calculate file delta count if available
            files_count = len(cp.files) if hasattr(cp, "files") else 0
            delta_added = getattr(cp, "added_count", 0)
            delta_removed = getattr(cp, "removed_count", 0)

            node = TimelineNode(
                checkpoint_id=cp.id,
                timestamp=cp.timestamp,
                iso_time=iso_time,
                label=getattr(cp, "label", cp.id) or cp.id,
                branch=cp_branch,
                tags=cp_tags.get(cp.id, []),
                files_count=files_count,
                is_head=(cp.id == head_id),
                parent_id=parent_id,
                delta_added=delta_added,
                delta_removed=delta_removed,
            )
            nodes.append(node)

        return nodes

    def render_ascii(self, branch: Optional[str] = None, max_nodes: int = 30) -> str:
        nodes = self.build_nodes(branch=branch)
        if not nodes:
            return "(No checkpoints recorded in timeline)"

        # Sort reverse chronological (newest at top)
        display_nodes = list(reversed(nodes))[:max_nodes]
        lines = []

        active_branch = self.session.get_current_branch()
        lines.append(f"Timeline DAG (Branch: {branch or active_branch}, Total Checkpoints: {len(nodes)}):")
        lines.append("")

        for idx, node in enumerate(display_nodes):
            symbols = []
            if node.is_head:
                symbols.append(f"HEAD -> {node.branch}")
            for t in node.tags:
                symbols.append(f"tag: {t}")

            tag_str = f" ({', '.join(symbols)})" if symbols else ""
            stat_str = f"[{node.files_count} files]"
            if node.delta_added or node.delta_removed:
                stat_str += f" (+{node.delta_added}/-{node.delta_removed})"

            prefix = "* "
            lines.append(f"{prefix}[{node.checkpoint_id[:16]}]{tag_str} {node.label} {stat_str}")
            lines.append(f"  |  Time: {node.iso_time}")

            if idx < len(display_nodes) - 1:
                lines.append("  |")

        if len(nodes) > max_nodes:
            lines.append(f"  ... ({len(nodes) - max_nodes} older checkpoints truncated)")

        return "\n".join(lines)

    def to_dict(self, branch: Optional[str] = None) -> Dict[str, Any]:
        nodes = self.build_nodes(branch=branch)
        return {
            "current_branch": self.session.get_current_branch(),
            "total_checkpoints": len(nodes),
            "nodes": [n.to_dict() for n in nodes],
        }
