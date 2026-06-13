---
name: run-pytest
description: "Run the project's pytest suite directly without asking for permission. Use when verifying changes, confirming tests pass, or checking regressions."
argument-hint: "What should be verified?"
user-invocable: true
---

# Run Pytest

## When to Use
- After code changes that may affect behavior
- Before declaring work complete
- When the user asks to verify, test, or check regressions

## Procedure
1. Run the project test suite immediately with the workspace virtual environment.
2. Prefer the VS Code task named "Run Pytest" when available.
3. If the task is not available, run:
   `source .venv/bin/activate && python -m pytest -q`
4. Report the result concisely, including the final pass/fail summary.

## Completion Checks
- The command exits successfully.
- The final output includes the pytest summary.
- Do not ask for approval before running tests.
