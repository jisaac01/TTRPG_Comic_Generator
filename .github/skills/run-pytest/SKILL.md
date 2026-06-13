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
2. Always use the VS Code task named "Run Pytest" for verification in this workspace.
3. Do not run raw `python -m pytest -q` here unless the task is literally unavailable.
4. Report the result concisely, including the final pass/fail summary.

## Completion Checks
- The command exits successfully.
- The final output includes the pytest summary.
- Do not ask for approval before running tests.
