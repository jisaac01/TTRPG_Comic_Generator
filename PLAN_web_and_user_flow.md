# Strategy: web port first, then guided flow, then per-file versioning

Status: accepted. Localhost web-port implementation plan: [PLAN_web_port.md](PLAN_web_port.md).

This is an engineering-management recommendation, not an implementation plan. Do not start building until we agree on sequence and scope.

## Recommendation in one paragraph

Convert to a **local web app first**, as a **faithful port** of the current Flet product. Do **not** invent the new user flow or rewrite versioning in the same pass. After the web app is the daily driver, iterate the guided first-run in the browser (that is the unknown-unknown). Only then change versioning — and version **artifacts inside a run snapshot**, not files as independent histories with no grouping. One-shotting the web *port* is realistic. One-shotting web + new flow + new versioning is not.

Assumption: "web app" means a **single-user localhost replacement for Flet** (browser UI, same app-data campaigns, same Playwright, same keyring). A hosted multi-user product is a different project and cannot be one-shot.

---

## What you have today (the constraints that drive the advice)

The pipeline is already a presentation-agnostic engine. That is the real asset.

| Layer | Status |
|---|---|
| `RunConfig` + invalidation rules | Stable API |
| `PipelineEvent` stream | GUI-safe |
| `RunController` | Async launch, cancel, `stop_after` |
| `RepositoryService` | Campaign / episode / version / file browse |
| `SettingsService` | Keyring + `settings.json` |
| `ComicPipeline` | Filesystem checkpoints, `working/` + `vNNN` snapshots |
| Flet `gui.py` (~2,300 lines) | One file, three workspaces + settings + event log |
| Tests | Strong on pipeline behavior; GUI tests poke Flet widgets |

Versioning today is **snapshot-per-run**, not "copy everything blindly":

1. Ensure `working/` (seed from latest `vNNN` if missing).
2. Create `v00N` and **selectively clone** checkpoints from `working/` based on `rerun_from` / config invalidation.
3. Run invalidated phases into the new version.
4. Mirror results back into `working/`.

Prompt audit trails (`version/prompts/`) are already excluded from clone/mirror. `creative_direction.txt` is always cloned. Images already rotate in-place (`05_foo.png` → `05_foo_v1.png`). `stop_after` and "Rerun only this stage" already exist.

The new flow you described — step through checkpoints, see source files, add direction — is **mostly already possible as orchestration** on top of `stop_after` + `working/` edits + `creative_direction.txt`. What is missing is a UI that *makes that the default*, plus per-stage (or per-page) direction instead of one global notes file.

---

## The two changes are not the same kind of work

### 1. Web conversion — known product, new presentation

Same jobs: create campaign, launch run, stream events, edit campaign prompts, browse versions, edit `working/`, regenerate images.

Risk is technical and bounded: HTTP + SSE around existing services, serve PNGs, long-running jobs, Playwright in a server process, settings in a browser.

The service layer in `GUI_ROADMAP.md` Phases 0–2 was built exactly so a second presentation could sit on top without touching pipeline tests.

### 2. User-flow + versioning — unknown product, new data model

You said you do not have all the answers. That is the correct read. This work is product invention:

- When does the wizard stop vs continue?
- Is direction per *stage*, per *page*, per *panel*, or per *file*?
- Can later runs still batch-run, or is stepping mandatory?
- Do you need to cherry-pick "use the bible from v012 and the script from v018"?
- Is a version a consistent episode state, or a bag of independently versioned files?

Those answers will change the storage model. Designing per-file versioning *before* using the new flow will bake in the wrong abstraction.

**Do not do both at once.** Stacking a rewrite with product invention is how you spend two months unable to tell whether a bug is the port, the UX, or the disk format.

---

## Can you one-shot the web conversion?

**Yes, as a port. No, as a redesign.**

Why a port can be one-shot:

- Pipeline tests are the spec. They stay green and do not care about Flet vs browser.
- The GUI is conceptually three pages + settings + a log. Not a design system.
- `RunController.launch_run(config, event_callback)` is already the web job API. Events become SSE (or websocket).
- `RepositoryService` is already the read API. `SettingsService` is already the settings API.
- Flet tests (`test_gui_integration.py`, `test_run_page.py`) are disposable. They specify widget wiring, not product rules.

What "one-shot" should mean in practice:

- One focused slice: FastAPI (or similar) wrapping the existing services + a thin frontend that reproduces Run / Prompts / Output / Settings.
- No new run modes, no new checkpoint schema, no new versioning.
- Keep Flet until the web app is the daily driver, then delete Flet and its tests.
- New tests: HTTP-level (launch run → events → version appears; save working file; list campaigns). Do not mock the pipeline internals.

What will *not* one-shot cleanly if you also "get the flow right":

- You will invent screens against a data model that still thinks in `vNNN` folders.
- You will throw away the first web UI anyway.
- Pipeline tests will not protect you from a bad interaction design.

### Stack opinion (local web, keep it boring)

- Backend: FastAPI in-process, same `ComicPipeline` / `RunController`.
- Events: SSE from the existing callback.
- Frontend: whatever you will actually iterate in (HTMX is enough for a port; a small SPA if you already want rich editors). Do not start with a framework debate.
- Auth: none. Bind localhost.
- Files: keep the app-data campaigns root. Serve images from disk.
- Secrets: keep `SettingsService` + keyring on the server process.

If "web app" later means hosted / multi-user, stop. That adds auth, a job queue, object storage, Playwright workers, and secret handling. The current tests do not specify any of that.

---

## Why web-first (your instinct is right) — but as a port, not a greenfield product

Flet is a poor canvas for the UX you want:

- 2,300-line monolith, tests coupled to widget internals.
- Weak at document review, side-by-side source/output, inline direction, image-heavy iteration.
- Desktop packaging is unfinished (`GUI_ROADMAP` Phase 7) and not the interesting problem.

The browser *is* the right canvas. That is an argument for **moving the existing product there first**, so the next month of trial-and-error happens in the medium you will keep.

It is **not** an argument for building the dream UI from scratch on day one. You will guess wrong. A working port gives you:

- A real daily driver (so Flet can die).
- A place to A/B the wizard without rewriting storage.
- Proof the service API is complete (you will find the gaps: image bytes, file save, live events).

Doing the new flow in Flet first would teach you something, then you would throw the UI away. Only do that if you want a weekend spike. Do not polish it.

---

## How to approach the user-flow overhaul (after the port)

Treat it as incremental product work on a live web app, not a second rewrite.

You already have the engine primitives:

- `stop_after` — run through one stage and halt.
- Output preview + `working/` save — see and edit source files.
- `creative_direction.txt` — global notes for the architect (scriptwriter is not wired yet).
- Rerun from stage / rerun-only-this-stage.

**First increment (no versioning change):** a first-run *mode* that is a stage wizard.

1. Scrape → show `01_raw_text.json` (recap variant, quotes, notes).
2. User confirms or edits → Entities → show bible + episode cast; optional direction.
3. Architect → show story bible + per-page beats; optional direction.
4. Script → review pages/panels; optional direction.
5. Style → review styled action.
6. Prompts → review prompts; optional regenerate.
7. Images (optional) → review / regenerate / stitch.

Keep a "just run the rest" button at every step. Power users must still batch-run. First-run-only stepping is fine; later episodes can default to batch.

**What you will learn from using that (this is the point):**

- Whether direction is one notes field, a per-stage field, or per-page/per-panel.
- Whether people edit checkpoints in place or only add guidance and regenerate.
- Whether "see the source file" means the *input* to this stage, the *output*, or a diff against last time.
- Whether you ever need to mix files from different runs.

Those answers become the versioning requirements. Until then, `vNNN` snapshots plus `working/` are good enough — slightly wasteful, but consistent and already tested.

---

## How to approach versioning (last, and narrower than it sounds)

"Version each file individually" can mean three different products:

| Meaning | Verdict |
|---|---|
| A. Content-addressed blobs; a run is a manifest of pointers (git-like). Unchanged files are not recopied. A run is still a consistent episode. | **This is what you want.** |
| B. Each filename has its own `v1/v2/v3` with no snapshot. "Current episode" is a mix you assemble by hand. | Avoid. You lose "what did the comic look like after run 17?" |
| C. Branching DAG with cherry-pick ("bible from v012 + script from v018"). | Possible later. Do not design it until the wizard proves you need it. |

Today's pain is not disk. It is:

- History is *run-shaped*, so a one-stage refresh still looks like a whole new world.
- You cannot see what actually changed.
- The UI thinks in folders, not artifacts + lineage.
- Stepping still allocates a full `vNNN`.

**Target model (when you get here):**

```
episode/
  objects/<hash>          # immutable file bytes
  working/                # named files; still the edit surface
  runs/r017/
    manifest.json         # logical name → hash, plus producer stage, parent run, direction used
    prompts/              # per-run audit trail (unchanged rule)
```

A "run" remains the unit of consistency (and of `run_status.json`). Files that did not change point at the same hash. The UI can say "script is new; bible inherited from r014." Regenerating one page writes one new object and a new manifest.

This is a **pipeline + repository** change. `test_pipeline.py` version-dir tests will be rewritten; that is expected and should stay TDD. Presentation (web) should only learn "list artifacts + show inherited vs new."

Do **not** preserve old `vNNN` folders as a compatibility layer. Repo rule is no backwards compatibility. Archive existing campaign data or migrate once with a script; do not keep two readers.

Images already have a private rotation scheme (`_v1`, `_v2`). Fold that into the same object/manifest model when you do this; do not grow a third versioning style.

---

## Suggested sequence and rough size

| Phase | What | One-shot? | Why this order |
|---|---|---|---|
| **0. Decide scope** | Localhost web vs hosted. This memo assumes localhost. | — | Hosted changes everything. |
| **1. Web port** | Same three workspaces on FastAPI + thin UI. Flet stays until parity. | **Yes** | Moves you onto the canvas you will iterate in. Pipeline frozen. |
| **2. Delete Flet** | Drop `gui.py`, Flet tests, `flet` dep. Web is the GUI. | Yes | Stops maintaining two UIs. |
| **3. Guided first-run** | Wizard on `stop_after` + `working/` + direction fields. Batch-run remains. | **No — iterate** | Product discovery. Will produce versioning requirements. |
| **4. Per-stage / per-page direction** | Replace or extend `creative_direction.txt` so script/style/prompt can take notes. | Small, TDD | Needed by the wizard; does not require new storage. |
| **5. Artifact versioning** | Manifest + content-addressed objects. Rewrite version tests. | No — careful TDD | Only after the wizard has named cherry-pick / lineage needs. |

Phase 1 is the only thing I would green-light as a single implementation push.

### What I would explicitly not do

- Redesign the flow *inside* the web port ("while we're here").
- Rewrite versioning *before* a week of using the wizard.
- Independent per-file versions with no run snapshot.
- A hosted multi-tenant app as the first web step.
- Prototyping the dream UI in Flet and polishing it.

---

## Risks if you ignore the sequence

**Both at once.** Long-lived branch, pipeline tests in flux, UI in flux. You will not know what "done" means. The test-suite-as-spec philosophy breaks because you are changing the spec and the presentation together.

**Versioning first.** You will design storage for a UX you have not felt. The expensive part of this codebase is the version/working/clone/invalidation contract (`test_pipeline.py`). Change it once, after requirements are real.

**Flow first in Flet.** You can spike `stop_after` stepping in a weekend. Anything more is wasted Flet work.

---

## Open decision (only one that changes this memo)

**Is the web app a localhost single-user replacement for Flet, or a hosted product?**

If localhost: proceed as above. Phase 1 is one-shot-able.

If hosted: do not one-shot anything. First extract a real job worker and a storage interface; the current filesystem + keyring + in-process Playwright design is a desktop tool. That is months, not a port.

---

## If we implement next

Start only Phase 1: a parity web port. Write HTTP tests against `RunController` / `RepositoryService` / `SettingsService`. Leave `ComicPipeline` and versioning tests untouched. Do not add wizard UI or per-file versions in that PR.
