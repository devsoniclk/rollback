# Contributing to rollback

We welcome contributions to `rollback`!

## Development Workflow
1. Fork and clone the repository.
2. Install editable package with dev dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
3. Run the test suite:
   ```bash
   pytest tests/ -v
   ```
4. Ensure all changes maintain sub-5ms micro-checkpoint and atomic rollback latency.

## Good First Issues
- Support for custom ignore glob patterns in `.rollbackignore`.
- Interactive TUI diff viewer.
- TypeScript / Node.js native binding wrapper.
