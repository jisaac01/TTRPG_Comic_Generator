# Copilot instructions

- Always use the existing VS Code task named "Run Pytest" for verification and regression checks.
- If you need a scoped test run, supply optional pytest arguments when the task prompts (for example, `tests/test_pipeline.py -k image_generation`); leave it blank for the full suite.
- Do not run raw `python -m pytest -q` in this workspace; use the task path instead.
- Keep the current LLM-backed continuity flow as the source of truth; avoid reintroducing deterministic merge-only assumptions.
