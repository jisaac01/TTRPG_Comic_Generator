---
name: run-pytest-focused
description: "Run a scoped pytest selection directly in the workspace virtual environment. Use for targeted regressions, single files, or -k filters such as tests/test_pipeline.py -k image_generation."
argument-hint: "Pytest selector, for example: tests/test_pipeline.py -k image_generation"
user-invocable: true
---

# Run Focused Pytest

## When to Use
- You need to verify one file, one class, or one pattern of tests.
- You want a fast regression check for a specific area of the codebase.
- The user explicitly asks for a scoped pytest run.

## Procedure
1. Run the focused test selection immediately in a terminal using proper venv activation.
2. Use this form: `source .venv/bin/activate && python -m pytest -q <selector>`
3. If no selector is provided, fall back to the full-suite skill named "run-pytest".
4. Report the result concisely, including the pass/fail summary.

## Examples
- `source .venv/bin/activate && python -m pytest -q tests/test_pipeline.py -k image_generation`
- `source .venv/bin/activate && python -m pytest -q tests/test_prompter.py`

## Completion Checks
- The command exits successfully.
- The final output includes the pytest summary.
- Do not ask for approval before running the test selection.
