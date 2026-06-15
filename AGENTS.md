# Agent instructions

Guidelines for AI agents working in this repository.

## Project purpose

TTRPG Comic Generator turns ScrybeQuill session recaps into structured comic scripts and optional comic images. A campaign-aware, versioned pipeline scrapes recap text, extracts entities, builds a story bible, writes per-page (or per-panel) scripts, applies art-direction styling, generates image prompts, and optionally sends those prompts to Gemini for image generation and stitching.

Primary entry points:

- **GUI**: `python src/main.py` (Flet app; recommended for runs and image generation)
- **CLI**: `python src/pipeline.py <campaign> <url>`
- **Tests**: `pytest` — the authoritative specification of behavior

See [README.md](README.md) for setup, checkpoint layout, and user-facing documentation.

## Development philosophy

This project is primarily a **test suite** from which the application is derived. The tests define required outputs and behaviors; implementation exists to satisfy them. It should be possible to rebuild the application from the test suite alone.

### Test-driven development (required)

1. **Write tests first.** Add or update failing tests that describe the desired behavior before changing production code.
2. **Focus on outputs and behaviors**, not implementation details. Assert on files written, JSON schemas, prompt text, pipeline events, GUI state, and end-to-end flows.
3. **Prefer functional tests over mock-heavy unit tests.** Use realistic fixtures, temp campaign directories, and checkpoint files. Accept more setup when it exercises real behavior.
4. **Mock only true external boundaries**: LLM API calls, Playwright scraping, Gemini image generation, keyring, and filesystem paths that must be isolated. Do not mock internal plumbing between project modules.
5. **Avoid negative/absence tests** (e.g. "no code path does X", "file must not contain Y"). Test what the system *does*, not what it avoids.
6. **Run tests before finishing work**: `source .venv/bin/activate && python -m pytest -q` (full suite) or a focused selector for the area you changed.

### Code style (required)

- **No backwards compatibility.** Implement the current schema and behavior only. Remove legacy paths, deprecated filenames, and compatibility shims rather than preserving them.
- **No fallbacks.** If required input is missing or invalid, fail clearly. Do not silently substitute defaults to paper over bad state.
- **No hacky workarounds.** Fix the root cause directly.
- **Keep it simple.** Prefer the smallest change that satisfies the tests. Avoid overengineering, extra abstraction layers, and speculative features.
- **Be concise** in code and comments. Match existing naming, types, and module layout.

## Architecture and code structure

```
src/
  main.py              # GUI entry point
  gui.py               # Flet UI (Run, Output, Settings tabs)
  pipeline.py          # ComicPipeline orchestrator and CLI
  pipeline_config.py   # RunConfig dataclass and rerun invalidation rules
  pipeline_events.py   # Structured events emitted during runs
  run_controller.py    # Async wrapper for GUI pipeline launches
  repository_service.py# Campaign/episode/version file browsing
  settings_service.py  # API keys, models (keyring + settings.json)
  app_paths.py         # Packaged vs dev path resolution

  scraper.py           # Phase 1: ScrybeQuill scrape → 01_raw_text.json
  entities.py          # Phase 2: entity extraction + LLM continuity merge
  master_beater.py     # Phase 3: story bible → 02_5_story_bible.json
  scriptwriter.py      # Phase 4: per-page or per-panel scripts
  style_integrator.py  # Phase 4.5: art-direction rewrite of setting/visual_action
  prompter.py          # Phase 5: image prompts from script + art direction
  prompt_saver.py      # Interpolated prompt templates saved under version/prompts/
  prompt_templates.py  # Template loading and rendering
  image_generator.py   # Phase 6: Gemini image API → 05_*.png
  image_stitcher.py    # Phase 7: panel images → 06_page_*.png (panel mode)

  llm_client.py        # Routes gemini-* → Gemini, others → Ollama
  model_defaults.py    # DEFAULT_MODEL

  prompts/             # Default prompt templates (copied into campaigns on first run)

tests/                 # Behavior specification; mirrors src/ modules
campaigns/             # Runtime campaign data (gitignored in real use)
```

## Pipeline flow

Each run creates a new version folder (`v001`, `v002`, …) under `campaigns/<campaign>/<episode>/`. Prior version checkpoints are cloned; only phases invalidated by `--rerun-from` or changed run settings are recomputed.

| Phase | Module | Key outputs |
|-------|--------|-------------|
| 1 Scrape | `scraper.py` | `01_raw_text.json` |
| 2 Entities | `entities.py` | `02_entities.json`, campaign `entities_bible.json` (LLM merge) |
| 3 Beater | `master_beater.py` | `02_5_story_bible.json`, `02_6_story_bible_page_*.json` |
| 4 Script | `scriptwriter.py` | `03_script_page_*.json` (panel mode also writes per-panel checkpoints) |
| 4.5 Style | `style_integrator.py` | `03_5_styled_script_page_*.json` |
| 5 Prompt | `prompter.py` | `04_page_*_prompt.txt` or `04_page_*_panel_*_prompt.txt` |
| 6 Images | `image_generator.py` | `05_*.png` (optional; requires `GEMINI_API_KEY`) |
| 7 Stitch | `image_stitcher.py` | `06_page_*.png` (panel mode only) |

**Generation modes** (`pipeline_config.GenerationMode`):

- `page` — one prompt and one image per page (default)
- `panel` — one prompt/image per panel, then stitch into `06_page_*.png`

**Run settings** persisted per version in `run_status.json`: `panel_count`, `total_pages`, `aspect_ratio`, `generation_mode`, `generate_images`, `recap_version`, `skip_style`, `rerun_from`.

Scene count for the master beater = `panel_count × total_pages`.

## Domain rules agents must preserve

- **LLM-backed entity continuity** is the source of truth (`entities._merge_entities_with_llm`). Do not replace it with deterministic-only merge logic.
- **Version immutability**: never overwrite an existing version folder; always create the next version.
- **Checkpoint skip logic**: within a version, skip a phase if its output files already exist (unless invalidated by rerun).
- **Prompt artifacts**: interpolated prompts are saved under `<version>/prompts/` for reproducibility.
- **Image generation** always uses Gemini via `ImageGenerator` and `build_openai_client`, regardless of which backend handles text stages.

## Testing conventions

- `tests/conftest.py` isolates credentials and `.env` leakage; do not rely on real API keys in tests.
- `tests/test_pipeline.py` is the main integration surface — study its fixtures before changing pipeline behavior.
- Pipeline tests patch external calls (`scrape_scrybequill`, LLM clients, `ImageGenerator`) at the boundary, then assert on written checkpoints and events.
- GUI tests (`test_gui_integration.py`, `test_run_page.py`) exercise layout and config wiring with lightweight fakes, not full Flet rendering.
- When adding a feature: add a failing test → implement → run focused tests → run full suite.

Focused test example:

```bash
source .venv/bin/activate && python -m pytest -q tests/test_pipeline.py -k image_generation
```

## What not to do

- Add CLI flags, GUI controls, or code paths solely for backwards compatibility with old checkpoint names or schemas.
- Introduce silent fallbacks (e.g. reading `03_script.json` when `03_script_page_001.json` is expected).
- Write tests that only assert mock call counts without checking outputs.
- Expand scope beyond the task (drive-by refactors, unrelated docs, extra config knobs).
- Commit `.env`, API keys, or generated campaign data.

## Useful references

- [README.md](README.md) — setup, CLI examples, checkpoint table
- [jargon.md](jargon.md) — comic terminology used in prompts and scripts
- [TODO.md](TODO.md) — active implementation plans
- [.github/skills/run-pytest/SKILL.md](.github/skills/run-pytest/SKILL.md) — full-suite test command