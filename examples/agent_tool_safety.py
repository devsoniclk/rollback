"""
Example: Integrating `rollback` with an autonomous coding agent loop.
Demonstrates transactional tool execution, dry-run diff inspection, and atomic rollbacks.
"""

import tempfile
from pathlib import Path
from rollback import create_session, compute_diff, transactional_tool

temp_base = tempfile.mkdtemp(prefix="rollback_example_")
workspace_dir = Path(temp_base) / "agent_ws"
workspace_dir.mkdir(parents=True, exist_ok=True)

# 1. Initialize rollback session
session = create_session(str(workspace_dir))

# Create an initial file
config_file = workspace_dir / "config.json"
config_file.write_text('{"env": "production", "debug": false}\n')

# 2. Capture baseline checkpoint
baseline = session.checkpoint("baseline_release")
print(f"Captured baseline checkpoint: {baseline.id}")

# 3. Simulate an Agent Tool Call with @transactional_tool
@transactional_tool(session=session, label="agent_edit_config")
def agent_dangerous_refactor(enable_chaos: bool):
    print("Agent attempting destructive multi-file modification...")
    config_file.write_text('{"env": "broken", "debug": true, "corrupted": true}\n')
    
    # Create an unwanted file
    (workspace_dir / "temp_leak.key").write_text("fake_private_key_do_not_publish\n")
    
    if enable_chaos:
        raise ValueError("Linter detected private key leak or syntax error!")

# 4. Execute tool under failure condition
try:
    agent_dangerous_refactor(enable_chaos=True)
except ValueError as e:
    print(f"\nCaught error: {e}")
    print("Rollback engine automatically restored original files!\n")

print(f"Config content after rollback: {config_file.read_text().strip()}")
print(f"Did temp_leak.key get deleted? {not (workspace_dir / 'temp_leak.key').exists()}")

# Clean up sample
import shutil
shutil.rmtree(workspace_dir, ignore_errors=True)
print("\nDemonstration complete.")
