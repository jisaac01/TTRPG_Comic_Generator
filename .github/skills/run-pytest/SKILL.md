---
name: run-pytest
description: "Run the project's full pytest suite directly without asking for permission. Use when verifying changes, confirming regressions, or checking the complete test baseline."
argument-hint: "Leave blank for the full suite."
user-invocable: true
---

# Run Pytest

## When to Use
- After code changes that may affect behavior
- Before declaring work complete
- When the user asks to verify, test, or check regressions

## Procedure
1. Run the project test suite immediately in a terminal using proper venv activation.
2. Use this command for the full suite: `source .venv/bin/activate && python -m pytest -q`
3. For scoped or focused pytest runs, use the dedicated focused-test skill or: `source .venv/bin/activate && python -m pytest -q <selector>`.
4. Report the result concisely, including the final pass/fail summary.

## Completion Checks
- The command exits successfully.
- The final output includes the pytest summary.
- Do not ask for approval before running tests.
