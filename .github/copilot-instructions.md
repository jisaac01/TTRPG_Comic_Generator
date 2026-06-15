# Copilot instructions

- For full-suite verification and regression checks, run the workspace command directly in a terminal: `source .venv/bin/activate && python -m pytest -q`.
- For scoped or focused pytest runs, use the dedicated focused-test workflow or run the selector directly in a terminal, for example `source .venv/bin/activate && python -m pytest -q tests/test_pipeline.py -k image_generation`.
- The `source .venv/bin/activate &&` prefix ensures the virtual environment is loaded before pytest runs.
- Keep the current LLM-backed continuity flow as the source of truth; avoid reintroducing deterministic merge-only assumptions.
