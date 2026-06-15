# Copilot instructions

Canonical agent guidelines: **[AGENTS.md](../AGENTS.md)**.

## Quick commands

- Full suite: `source .venv/bin/activate && python -m pytest -q`
- Focused run: `source .venv/bin/activate && python -m pytest -q tests/test_pipeline.py -k image_generation`

Always activate the venv before pytest. Write tests first; assert on outputs and behaviors, not implementation plumbing.

## Project-specific reminders

- Tests are the source of truth — the app is generated to satisfy them.
- No backwards compatibility, no legacy fallbacks, no hacky workarounds.
- Entity continuity uses the LLM merge path in `entities.py`; do not revert to deterministic-only merge.
- Pipeline runs are versioned under `campaigns/<campaign>/<episode>/vNNN/`; never overwrite prior versions.