from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import RollbackSession, Checkpoint
    from .diff import DiffSummary


def _format_timestamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _generate_timeline_svg(checkpoints: List[Any], branches: Dict[str, str], current_branch: str) -> str:
    """Generates an embedded responsive SVG timeline DAG of checkpoints."""
    count = len(checkpoints)
    if count == 0:
        return "<p style='color:#64748b;text-align:center;'>No checkpoints recorded yet.</p>"

    # Display up to 12 recent checkpoints in visual timeline
    display_cps = checkpoints[-12:]
    node_count = len(display_cps)
    width = max(600, node_count * 130 + 100)
    height = 180
    y_pos = 90

    svg_elements = []

    # Connection line between checkpoints
    x_positions = []
    step_width = (width - 120) / max(1, node_count - 1) if node_count > 1 else width / 2

    for i in range(node_count):
        x = 60 + i * step_width if node_count > 1 else width / 2
        x_positions.append(x)

    # Draw timeline connecting lines
    for i in range(node_count - 1):
        x1 = x_positions[i]
        x2 = x_positions[i + 1]
        svg_elements.append(
            f'<line x1="{x1}" y1="{y_pos}" x2="{x2}" y2="{y_pos}" stroke="#334155" stroke-width="2.5" />'
        )

    # Inverted lookup for branch heads
    branch_heads = {cid: bname for bname, cid in branches.items()}

    # Draw nodes
    for i, cp in enumerate(display_cps):
        cx = x_positions[i]
        is_latest = (i == node_count - 1)
        node_color = "#3b82f6" if cp.branch == current_branch else "#8b5cf6"
        stroke_color = "#60a5fa" if is_latest else "#475569"

        # Node circle
        svg_elements.append(
            f'<circle cx="{cx}" cy="{y_pos}" r="14" fill="#1e293b" stroke="{stroke_color}" stroke-width="2.5" />'
        )
        svg_elements.append(
            f'<circle cx="{cx}" cy="{y_pos}" r="6" fill="{node_color}" />'
        )

        # Checkpoint Short ID
        cid_short = cp.id[:7]
        svg_elements.append(
            f'<text x="{cx}" y="{y_pos - 24}" fill="#f1f5f9" font-family="ui-monospace, monospace" font-size="11" font-weight="600" text-anchor="middle">{cid_short}</text>'
        )

        # Label / description
        lbl_escaped = html.escape(cp.label[:14] + ("…" if len(cp.label) > 14 else ""))
        svg_elements.append(
            f'<text x="{cx}" y="{y_pos + 30}" fill="#94a3b8" font-family="-apple-system, sans-serif" font-size="10" text-anchor="middle">{lbl_escaped}</text>'
        )

        # Branch badge if branch head
        if cp.id in branch_heads:
            b_name = html.escape(branch_heads[cp.id])
            svg_elements.append(
                f'<rect x="{cx - 32}" y="{y_pos - 48}" width="64" height="18" rx="4" fill="#0f766e" />'
                f'<text x="{cx}" y="{y_pos - 35}" fill="#ccfbf1" font-family="ui-monospace, monospace" font-size="9" font-weight="700" text-anchor="middle">{b_name}</text>'
            )

    return f'''<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" style="overflow:visible;" xmlns="http://www.w3.org/2000/svg">
  {"".join(svg_elements)}
</svg>'''


def render_html_report(
    session: RollbackSession,
    output_path: Optional[Union[str, Path]] = None,
    title: str = "Rollback Session Dossier",
) -> str:
    """
    Renders an interactive dark-mode HTML visual dossier for the current RollbackSession.
    Visualizes checkpoint DAG, branch topologies, file deltas, and security policies.
    """
    from .diff import compute_diff

    # Gather metadata
    checkpoints = [session.get_checkpoint(cid) for cid in session.history if session.get_checkpoint(cid)]
    total_cps = len(checkpoints)
    active_branch = session.current_branch
    head_cp = checkpoints[-1] if checkpoints else None
    head_id = head_cp.id[:8] if head_cp else "none"

    # Compute blob directory size
    blob_count = 0
    blob_bytes = 0
    if session.blobs_dir.exists():
        for b in session.blobs_dir.glob("*"):
            if b.is_file():
                blob_count += 1
                blob_bytes += b.stat().st_size

    def _format_bytes(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.2f} MB"

    storage_str = f"{_format_bytes(blob_bytes)} ({blob_count} blobs)"

    # Compute latest diff if head exists
    diff_summary = None
    if head_cp:
        try:
            diff_summary = compute_diff(session, from_checkpoint=head_cp.id)
        except Exception:
            diff_summary = None

    # Generate timeline SVG
    timeline_svg = _generate_timeline_svg(checkpoints, session.branches, active_branch)

    # Security policy details
    has_sec = session.security_policy is not None
    sec_status_color = "#10b981" if has_sec else "#64748b"
    sec_status_text = "ENFORCED" if has_sec else "PERMISSIVE"
    protected_patterns = (
        ", ".join(session.security_policy.protected_patterns) if has_sec and session.security_policy else "None"
    )

    # Checkpoint rows HTML
    cp_rows_html = []
    for cp in reversed(checkpoints):
        cid = cp.id
        cid_short = cid[:8]
        ts_str = _format_timestamp(cp.timestamp)
        file_count = len(cp.files)
        branch_badge = (
            f'<span class="badge badge-branch">{html.escape(cp.branch)}</span>'
            if cp.branch != "main"
            else '<span class="badge badge-main">main</span>'
        )

        # File changes in this checkpoint
        files_html = []
        for p, fs in sorted(cp.files.items()):
            path_esc = html.escape(p)
            sz_str = _format_bytes(fs.size)
            if not fs.exists:
                files_html.append(f'<div class="file-item deleted"><span class="tag tag-del">DELETED</span> {path_esc}</div>')
            else:
                files_html.append(f'<div class="file-item"><span class="tag tag-ok">TRACKED</span> {path_esc} <span class="size-hint">({sz_str})</span></div>')

        files_accordion = (
            f'<div class="files-list">{"".join(files_html)}</div>'
            if files_html
            else '<div class="files-list empty">No files tracked in this checkpoint.</div>'
        )

        cp_rows_html.append(f'''
        <div class="cp-card">
          <div class="cp-header">
            <div class="cp-title">
              <span class="cp-id">{cid_short}</span>
              <span class="cp-label">{html.escape(cp.label)}</span>
              {branch_badge}
            </div>
            <div class="cp-meta">
              <span>{ts_str}</span>
              <span class="badge badge-count">{file_count} files</span>
            </div>
          </div>
          {files_accordion}
        </div>
        ''')

    # Build Diff Summary section
    diff_html = ""
    if diff_summary:
        mod_count = len(diff_summary.modified)
        add_count = len(diff_summary.added)
        del_count = len(diff_summary.deleted)
        clean = diff_summary.is_clean

        if clean:
            diff_html = '<div class="notice notice-ok">Workspace matches HEAD checkpoint state. Working tree is clean.</div>'
        else:
            diff_items = []
            for item in diff_summary.added:
                diff_items.append(f'<li class="diff-add">+ Added: <code>{html.escape(item)}</code></li>')
            for item in diff_summary.modified:
                diff_items.append(f'<li class="diff-mod">~ Modified: <code>{html.escape(item)}</code></li>')
            for item in diff_summary.deleted:
                diff_items.append(f'<li class="diff-del">- Deleted: <code>{html.escape(item)}</code></li>')

            diff_html = f'''
            <div class="diff-box">
              <div class="diff-metrics">
                <span class="badge badge-add">+{add_count} added</span>
                <span class="badge badge-mod">~{mod_count} modified</span>
                <span class="badge badge-del">-{del_count} deleted</span>
              </div>
              <ul class="diff-tree">
                {"".join(diff_items)}
              </ul>
            </div>
            '''
    else:
        diff_html = '<div class="notice">No active diff available (no checkpoints initialized).</div>'

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)} — rollback</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f2937;
      --header-bg: #1e293b;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #3b82f6;
      --accent-glow: rgba(59, 130, 246, 0.2);
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --purple: #8b5cf6;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 32px 20px;
      line-height: 1.5;
    }}
    .container {{
      max-width: 1080px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .logo-badge {{
      background: linear-gradient(135deg, #2563eb, #7c3aed);
      color: #fff;
      font-weight: 800;
      font-size: 14px;
      padding: 6px 10px;
      border-radius: 6px;
      font-family: ui-monospace, monospace;
      letter-spacing: 0.5px;
    }}
    h1 {{
      font-size: 22px;
      font-weight: 700;
      color: var(--text);
    }}
    .root-pill {{
      background: #1e293b;
      color: var(--text-muted);
      padding: 6px 12px;
      border-radius: 6px;
      font-family: ui-monospace, monospace;
      font-size: 12px;
      border: 1px solid #334155;
    }}
    .grid-stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 16px;
      margin-bottom: 28px;
    }}
    .stat-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px 20px;
      position: relative;
      overflow: hidden;
    }}
    .stat-card::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0; height: 3px;
      background: var(--accent);
    }}
    .stat-card.sec::before {{ background: var(--success); }}
    .stat-card.storage::before {{ background: var(--purple); }}
    .stat-card.branch::before {{ background: var(--warning); }}
    .stat-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-muted);
      font-weight: 600;
      margin-bottom: 6px;
    }}
    .stat-val {{
      font-size: 22px;
      font-weight: 700;
      font-family: ui-monospace, monospace;
    }}
    .section {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 24px;
      margin-bottom: 24px;
    }}
    .section-title {{
      font-size: 15px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      margin-bottom: 16px;
      color: #cbd5e1;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .timeline-container {{
      background: #0d131f;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 20px 10px;
      overflow-x: auto;
    }}
    .cp-card {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
      margin-bottom: 12px;
      overflow: hidden;
    }}
    .cp-header {{
      padding: 12px 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #172033;
      border-bottom: 1px solid #1e293b;
    }}
    .cp-title {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .cp-id {{
      font-family: ui-monospace, monospace;
      font-size: 12px;
      font-weight: 700;
      color: #60a5fa;
      background: #1e3a8a;
      padding: 3px 8px;
      border-radius: 4px;
    }}
    .cp-label {{
      font-weight: 600;
      font-size: 13px;
    }}
    .cp-meta {{
      font-size: 11px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .badge {{
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 4px;
      font-family: ui-monospace, monospace;
      text-transform: uppercase;
    }}
    .badge-main {{ background: #1e293b; color: #94a3b8; }}
    .badge-branch {{ background: #581c87; color: #e9d5ff; }}
    .badge-count {{ background: #1e293b; color: #cbd5e1; }}
    .badge-add {{ background: #064e3b; color: #a7f3d0; }}
    .badge-mod {{ background: #78350f; color: #fde68a; }}
    .badge-del {{ background: #7f1d1d; color: #fecaca; }}
    .files-list {{
      padding: 10px 16px;
      font-size: 12px;
      font-family: ui-monospace, monospace;
    }}
    .files-list.empty {{
      color: var(--text-muted);
      font-style: italic;
    }}
    .file-item {{
      padding: 4px 0;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .file-item.deleted {{ color: #f87171; text-decoration: line-through; }}
    .tag {{
      font-size: 9px;
      padding: 1px 4px;
      border-radius: 3px;
      font-weight: 700;
    }}
    .tag-ok {{ background: #065f46; color: #34d399; }}
    .tag-del {{ background: #991b1b; color: #fca5a5; }}
    .size-hint {{ color: var(--text-muted); font-size: 10px; }}
    .diff-box {{
      background: #0d131f;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 16px;
    }}
    .diff-metrics {{
      display: flex;
      gap: 10px;
      margin-bottom: 12px;
    }}
    .diff-tree {{
      list-style: none;
      font-family: ui-monospace, monospace;
      font-size: 12px;
    }}
    .diff-tree li {{ padding: 3px 0; }}
    .diff-add {{ color: #34d399; }}
    .diff-mod {{ color: #fbbf24; }}
    .diff-del {{ color: #f87171; }}
    .notice {{
      padding: 12px 16px;
      background: #1e293b;
      border-radius: 6px;
      font-size: 12px;
      color: var(--text-muted);
    }}
    .notice-ok {{
      background: #064e3b;
      color: #a7f3d0;
      border: 1px solid #059669;
    }}
    .sec-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}
    .sec-table th, .sec-table td {{
      padding: 8px 12px;
      text-align: left;
      border-bottom: 1px solid #1e293b;
    }}
    .sec-table th {{ color: var(--text-muted); font-weight: 600; }}
    footer {{
      margin-top: 32px;
      text-align: center;
      font-size: 11px;
      color: #475569;
      border-top: 1px solid #1e293b;
      padding-top: 20px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <span class="logo-badge">ROLLBACK</span>
        <h1>{html.escape(title)}</h1>
      </div>
      <div class="root-pill">ROOT: {html.escape(str(session.root))}</div>
    </header>

    <div class="grid-stats">
      <div class="stat-card">
        <div class="stat-label">Total Checkpoints</div>
        <div class="stat-val">{total_cps}</div>
      </div>
      <div class="stat-card branch">
        <div class="stat-label">Active Branch / Head</div>
        <div class="stat-val" style="color:#fbbf24;">{html.escape(active_branch)} <span style="font-size:13px;color:#94a3b8;">({head_id})</span></div>
      </div>
      <div class="stat-card storage">
        <div class="stat-label">Journal Storage</div>
        <div class="stat-val" style="color:#c084fc;">{storage_str}</div>
      </div>
      <div class="stat-card sec">
        <div class="stat-label">Security Containment</div>
        <div class="stat-val" style="color:{sec_status_color};">{sec_status_text}</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">
        <span>Timeline & Checkpoint DAG</span>
        <span style="font-size:11px;color:#94a3b8;">Showing latest checkpoints</span>
      </div>
      <div class="timeline-container">
        {timeline_svg}
      </div>
    </div>

    <div class="section">
      <div class="section-title">
        <span>Active Workspace Diff vs HEAD</span>
      </div>
      {diff_html}
    </div>

    <div class="section">
      <div class="section-title">
        <span>Security Policy & Containment Guard</span>
      </div>
      <table class="sec-table">
        <tr>
          <th>Guard Rule</th>
          <th>Configuration Status</th>
        </tr>
        <tr>
          <td>Workspace Root Boundary</td>
          <td><span style="color:#34d399;font-weight:600;">ENFORCED</span> (traversal escapes ../ strictly rejected)</td>
        </tr>
        <tr>
          <td>Symlink Jail Defense</td>
          <td><span style="color:#34d399;font-weight:600;">ACTIVE</span> (resolves physical inodes to prevent symlink bypass)</td>
        </tr>
        <tr>
          <td>Protected Secret Patterns</td>
          <td><code>{html.escape(protected_patterns)}</code></td>
        </tr>
      </table>
    </div>

    <div class="section">
      <div class="section-title">
        <span>Checkpoint History ({total_cps})</span>
      </div>
      {"".join(cp_rows_html) if cp_rows_html else '<p style="color:#94a3b8;font-size:12px;">No checkpoints recorded.</p>'}
    </div>

    <footer>
      Generated by <strong>rollback-engine v0.4.0</strong> — Transactional State & Filesystem Revert Engine for AI Coding Agents.
    </footer>
  </div>
</body>
</html>
'''

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(html_content, encoding="utf-8")

    return html_content
