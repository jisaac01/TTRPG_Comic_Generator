# Copilot instructions

- Always use the existing VS Code task named "Run Pytest" for verification and regression checks.
- Do not run raw `python -m pytest -q` in this workspace; use the task path instead.
- Keep the current LLM-backed continuity flow as the source of truth; avoid reintroducing deterministic merge-only assumptions.
