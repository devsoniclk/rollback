from __future__ import annotations

import tempfile
from pathlib import Path
from rollback.core import RollbackSession
from rollback.html_report import render_html_report
from rollback.security import SecurityPolicy


def test_html_report_generation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        sec = SecurityPolicy(allowed_root=root, protected_patterns=[".env", "*.secret"])
        session = RollbackSession(root_dir=str(root), security_policy=sec)

        # Create files & checkpoints
        f1 = root / "app.py"
        f1.write_text("print('v1')")
        cp1 = session.checkpoint(label="initial_app")

        f1.write_text("print('v2')")
        f2 = root / "utils.py"
        f2.write_text("def helper(): pass")
        cp2 = session.checkpoint(label="add_utils")

        # Test in-memory rendering
        html_out = render_html_report(session, title="Test Lab Dossier")
        assert "<!DOCTYPE html>" in html_out
        assert "Test Lab Dossier" in html_out
        assert "ROLLBACK" in html_out
        assert "initial_app" in html_out
        assert "add_utils" in html_out
        assert "<svg" in html_out
        assert "app.py" in html_out
        assert "utils.py" in html_out
        assert "ENFORCED" in html_out
        assert ".env" in html_out

        # Test writing to file
        report_file = root / "report.html"
        res = session.to_html(output_path=report_file)
        assert report_file.exists()
        assert len(report_file.read_text()) > 500
        assert res == report_file.read_text()
