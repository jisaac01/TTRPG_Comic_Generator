---
name: run-pytest
description: "Run the project's pytest suite directly, including focused subsets, without asking for permission. Use when verifying changes, confirming regressions, or running a scoped test selection such as tests/test_pipeline.py -k image_generation."
argument-hint: "Optional pytest arguments; leave blank for the full suite."
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
3. If you need a scoped subset, provide optional pytest arguments when prompted, such as `tests/test_pipeline.py -k image_generation`; leave the prompt blank for the full suite.
4. Do not run raw `python -m pytest -q`. If for some reason "Run Pytest" is not available, try to fix the problem with the task configuration rather than bypassing it.
5. Report the result concisely, including the final pass/fail summary.

## Completion Checks
- The command exits successfully.
- The final output includes the pytest summary.
- Do not ask for approval before running tests.
