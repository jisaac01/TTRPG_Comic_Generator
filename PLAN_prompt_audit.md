# Plan: Unify prompt template + FINAL audit capture

**Status:** planned (not implemented)  
**Related TODO:** [TODO.md](TODO.md) — “Unify prompt audit capture”

## Requirement

> Save every prompt template and its rendered final form under `version/prompts/` for any text sent to an LLM.

That should be a single, hard-to-bypass path—not a growing set of per-stage `prepare_*` helpers.

## Problem (current design)

### 1. “Save template + FINAL” is not one operation

Each stage reimplements a partial version of audit I/O:

- different FINAL filename rules (`_FINAL`, `_FINAL_page_001`, vignette vs standard names)
- different variable-building logic mixed with filesystem writes
- call sites that may or may not invoke the saver

Adding “entities continuity FINAL” or “vignette FINAL under the right name” is not “write two files.” It is wiring another stage into an accidental pattern.

### 2. Two parallel systems

| Mechanism | When | What it saves |
|-----------|------|----------------|
| `_capture_prompt_templates_for_version` | Start of run | All known template files (bulk copy) |
| `prepare_*_prompts` | At each LLM call | Template again + rendered FINAL |

Bulk capture already covers “save every template.” FINALs live only in decentralized `prepare_*` paths—and those paths sometimes re-save templates under the wrong names (e.g. vignette content as `story_architect_*`).

### 3. Template identity was allowed to lie

Vignette mode historically remapped source paths onto standard keys so FINALs stayed `story_architect_*_FINAL.txt`. That optimized “one prepare_architect path” at the cost of “FINAL name ≠ real template name.”

Rule that should hold: **FINAL stem = basename of the template path actually used.**

### 4. Entities was a second mini-pipeline

`_merge_entities_with_llm` used `render_prompt_template(name=…)` → system defaults, not campaign/version paths, and did not go through `prompt_saver`. Continuity was outside the audit product until patched ad hoc.

### 5. Stage prep is mixed with audit I/O

Each `prepare_*` does three jobs:

1. Stage domain work (alias normalize, format entities, page-1 directive, art blocks, …)
2. Template render
3. Audit filesystem writes

(1) must stay stage-specific. (2)+(3) should not be reimplemented per stage.

### 6. Error-prone by structure

New prompts require touching multiple manual lists:

- filename constants in `prompt_templates.py`
- `PROMPT_TEMPLATE_FILENAMES` for bootstrap
- `_resolve_prompt_templates` dict
- a `prepare_*` function
- a pipeline (or entities) call site

There is no rule: *any text sent to an LLM must go through `audit_prompt(...)`.*

## Target model

```
resolve template path (campaign / override)
  → build values dict (stage-specific)
    → render_and_capture(version_dir, template_path, values, suffix=?)
         copies path.name → version/prompts/
         writes {stem}_FINAL.txt or {stem}_FINAL_{suffix}.txt
         returns rendered string
  → send returned string to model
```

Stages own only: which path, which values, optional suffix. Audit is one function.

## Recommended work (priority order)

### A. One primitive (highest leverage)

Add something like:

```text
render_and_capture(version_dir, template_path, *, values, suffix=None) -> str
```

Rules:

- Template archive name = `template_path.name`
- FINAL name = `{stem}_FINAL.txt` or `{stem}_FINAL_{suffix}.txt`
- No special cases for vignette / continuity / scriptwriter—names follow paths
- Stages only supply `template_path` + `values` (+ optional `suffix`)

Then shrink or delete `prepare_architect_prompts`, `prepare_scriptwriter_prompts`, etc. into thin “build values + call primitive” wrappers (or move values-building into the stage modules).

### B. One mandatory chokepoint for model-bound text

Policy: **no raw `render_prompt_template` on paths that hit an LLM** outside the audit helper.

- Entities continuity merge goes through it
- Architect / script / style / page prompts go through it
- Prefer tests that assert rendered prompts used in a stage left a FINAL

### C. Stop remapping template identity

Resolve and use real paths:

- standard architect → `story_architect_*.txt`
- vignette → `story_architect_vignette_*.txt`

Never copy vignette content onto the standard filename for convenience. Bulk capture may still dump both; FINAL only for the one used.

*(Partial progress already exists post-vignette/continuity FINAL work; keep this rule when consolidating.)*

### D. Collapse the dual capture paths

Pick one story:

**D1 — capture-on-use only**  
When you render, archive that template + FINAL. Drop bulk copy of every template at run start.  
*Pro:* only what was used. *Con:* unused campaign templates not in the version.

**D2 — bulk templates once + FINAL-only at use (recommended default)**  
Keep `_capture_prompt_templates_for_version` for “all campaign templates this run could use.” At call time, **only write FINALs**—do not re-copy templates inside each prepare.  
*Pro:* full template set + lean per-stage code. *Con:* version may include unused templates (current behavior).

Either way, delete “prepare re-saves the template” duplication.

### E. Keep audit out of domain modules as a special case

Prefer orchestration (pipeline / shared continuity step) to:

1. resolve continuity template paths  
2. `render_and_capture(...)`  
3. call merge with the rendered strings  

`entities` stays “merge these world states given prompt texts.” Audit stays a pipeline/prompt concern.

### F. Optional: template registry

One registry for roles/filenames/when-used, shared by:

- bootstrap into campaign root  
- resolve for a run  
- bulk capture  

FINALs still require runtime values; the registry only stops forgetting a filename in three places.

## Suggested implementation phases

1. **Introduce `render_and_capture`** (or equivalent name) with tests on naming + contents.  
2. **Migrate one stage** (e.g. architect or style) end-to-end; delete its bespoke save/FINAL logic.  
3. **Migrate remaining stages** including entities continuity and page prompts.  
4. **Choose D1 or D2**; remove duplicate template re-copy.  
5. **Enforce chokepoint** (code review + tests; no direct render for LLM call sites).  
6. **Optional registry** if bootstrap/resolve lists are still painful.

## Out of scope / non-goals

- Changing campaign-root vs system-default resolution policy (campaign remains editable source of truth; system defaults bootstrap).  
- Moving editable templates into `working/` (prompts are campaign-lifetime; `version/prompts/` is audit-only; not mirrored into working).  
- Silent multi-tier fallback chains (working → campaign → system).

## Success criteria

- Adding a new LLM-facing prompt template requires: new file + resolve entry (or registry) + one `render_and_capture` call with a values dict—not a new `prepare_*` module function.  
- FINAL filename always matches the template path actually used.  
- Every model-bound rendered string for a run has a corresponding FINAL under that version’s `prompts/`.  
- No stage reimplements template copy + FINAL write.

## Context

Discussed after working-dir / prompt-paradigm cleanup and ad hoc FINAL capture for `entities_continuity_*` and `story_architect_vignette_*`. Those patches closed gaps but confirmed the design tax of per-stage prepare helpers.
