import tempfile
from pathlib import Path
from rollback.core import RollbackSession
from rollback.timeline import TimelineGraph, TimelineNode


def test_timeline_graph_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        tl = session.timeline
        assert isinstance(tl, TimelineGraph)

        # 1. Empty timeline
        empty_ascii = tl.render_ascii()
        assert "No checkpoints recorded" in empty_ascii
        assert tl.to_dict()["total_checkpoints"] == 0

        # 2. Add checkpoints on main
        f1 = Path(tmpdir) / "file1.txt"
        f1.write_text("initial commit\n")
        cp1 = session.checkpoint("initial checkpoint")
        session.tag(cp1.id, "v0.1.0")

        f1.write_text("update 1\n")
        cp2 = session.checkpoint("second checkpoint")

        # 3. Create branch and add checkpoint
        session.create_branch("feature-x")
        session.checkout("feature-x")
        f2 = Path(tmpdir) / "feature.py"
        f2.write_text("print('feature')\n")
        cp3 = session.checkpoint("feature checkpoint")

        # 4. Build nodes
        nodes = tl.build_nodes()
        assert len(nodes) == 3
        assert all(isinstance(n, TimelineNode) for n in nodes)

        # Check nodes content
        node_map = {n.checkpoint_id: n for n in nodes}
        assert node_map[cp1.id].label == "initial checkpoint"
        assert "v0.1.0" in node_map[cp1.id].tags
        assert node_map[cp2.id].parent_id == cp1.id
        assert node_map[cp3.id].branch == "feature-x"
        assert node_map[cp3.id].is_head is True

        # 5. Filter by branch
        main_nodes = tl.build_nodes(branch="main")
        assert len(main_nodes) == 2
        feat_nodes = tl.build_nodes(branch="feature-x")
        assert len(feat_nodes) == 1

        # 6. Render ASCII
        ascii_out = tl.render_ascii()
        assert "Timeline DAG" in ascii_out
        assert "initial checkpoint" in ascii_out
        assert "tag: v0.1.0" in ascii_out
        assert "HEAD -> feature-x" in ascii_out

        # 7. To dict
        d = tl.to_dict()
        assert d["total_checkpoints"] == 3
        assert d["current_branch"] == "feature-x"
        assert len(d["nodes"]) == 3


def test_timeline_truncation_and_formatting() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        session = RollbackSession(tmpdir)
        tl = session.timeline

        for i in range(10):
            (Path(tmpdir) / f"file_{i}.txt").write_text(f"content {i}\n")
            session.checkpoint(f"batch_commit_{i}")

        nodes = tl.build_nodes()
        assert len(nodes) == 10

        # Render ASCII with max_nodes limit
        rendered = tl.render_ascii(max_nodes=4)
        assert "older checkpoints truncated" in rendered
        assert "batch_commit_9" in rendered
