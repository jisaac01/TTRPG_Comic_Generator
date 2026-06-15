# TTRPG Comic Generator

Converts a ScrybeQuill session recap into a structured comic script via a campaign-aware, versioned, checkpoint-resumable pipeline — then optionally generates comic images with Gemini.

Each run is isolated in its own version folder. Prior runs are never overwritten, so you can compare outputs across art style changes, text corrections, model switches, or generation modes.

## Requirements

- Python 3.12+
- One of the following model backends configured for the text models selected in `src/model_defaults.py`:
  - [Ollama](https://ollama.com) running locally for non-`gemini-` models
  - Google Gemini API access via `GEMINI_API_KEY` for models whose name starts with `gemini-`
- **Image generation** (optional): a Gemini image model (default: `gemini-2.5-flash-image`) and a valid `GEMINI_API_KEY`. Image generation uses Gemini's OpenAI-compatible image API regardless of which backend you use for text stages.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

On Windows PowerShell, activate the virtual environment with:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

**Windows only:** Playwright requires the Visual C++ Redistributable. If you get a `DLL load failed` error when scraping, install it:

```
winget install Microsoft.VCRedist.2015+.x64
```

Then restart your terminal and re-run `python -m playwright install chromium`.

## Building for Windows

The build must be run **on a Windows machine**. Prerequisites (one-time):

1. Install **Visual Studio 2022+** with the "Desktop development with C++" workload.
2. Install the VC++ Redistributable (also required at runtime for Playwright):
   ```powershell
   winget install Microsoft.VCRedist.2015+.x64
   ```

Flutter SDK is required by `flet build` but does **not** need to be installed manually — on first run, `flet build` will detect it is missing and offer to download it automatically.

Build command (run from project root):

```powershell
# Bundle Chromium into the packaged app (run in the same venv used for build)
$env:PLAYWRIGHT_BROWSERS_PATH="src/playwright-browsers"
python -m playwright install chromium

# Build the EXE
flet build windows
```

Verify Chromium was bundled into the build output:

```powershell
Get-ChildItem -Recurse .\build\windows -Filter chrome-headless-shell.exe
```

Output is placed in `build/windows/`. The build configuration is in `pyproject.toml` — the `src/` directory is packaged as the application root, with `src/main.py` as the entry point.

With the commands above, Chromium is bundled into the app, so end users do not need to run Playwright install commands.

## Runtime paths

Runtime behavior for packaged builds:

- Campaign data is stored in the user data directory, not beside the executable.
  - Windows: `%LOCALAPPDATA%/TTRPG_Comic_Generator/campaigns`
  - macOS: `~/Library/Application Support/TTRPG_Comic_Generator/campaigns`
  - Linux: `${XDG_DATA_HOME:-~/.local/share}/TTRPG_Comic_Generator/campaigns`
- Settings are stored in the same app data root as `settings.json`.
- Default prompt templates are loaded from the packaged `prompts/` directory and copied into campaign folders on first run.

Optional overrides:

- `COMIC_GENERATOR_CAMPAIGNS_ROOT`
- `COMIC_GENERATOR_PROMPTS_DIR`
- `COMIC_GENERATOR_CONFIG_PATH`
- `COMIC_GENERATOR_APP_DATA_ROOT`

Playwright prerequisites for Windows users:

- Install VC++ runtime if needed:

```powershell
winget install Microsoft.VCRedist.2015+.x64
```

The GUI shows a startup warning when Playwright runtime prerequisites appear to be missing.

If you are using Gemini, set `GEMINI_API_KEY` in your environment, in a local `.env` file, or in the GUI Settings tab (stored via the system keyring).

If you are using Ollama, make sure Ollama is running locally and the selected model is available. `OLLAMA_BASE_URL` defaults to `http://localhost:11434/v1`.

## GUI

The Flet GUI is the recommended way to run the pipeline and manage image generation.

```bash
python src/main.py
(python src/gui.py)
```

The GUI provides:

- **Run tab** — configure and launch pipeline runs with campaign, URL, recap variant, panel/page counts, aspect ratio, generation mode, and optional image generation.
- **Output tab** — browse version checkpoints, preview prompt files, regenerate individual images, generate all images for a version, and stitch panel images into finished pages.
- **Settings tab** — store your Gemini API key, default text model, and image generation model.

## Campaign setup

Each campaign has its own folder under `campaigns/`. On the first pipeline run, the campaign folder is bootstrapped automatically with reusable defaults for:

- `master_beater_system.txt`
- `master_beater_user.txt`
- `art_direction_template.json`
- `scriptwriter_system.txt`
- `scriptwriter_user.txt`
- `style_integrator_system.txt`
- `style_integrator_user.txt`
- `entities_continuity_system.txt`
- `entities_continuity_user.txt`
- `page_prompt.txt`

All default files are copied from the shared `src/prompts/` directory. Edit the campaign copies when you want campaign-specific behavior.

If you want to pre-seed defaults manually, copy them the same way:

```bash
mkdir -p campaigns/dreadmarsh
cp src/prompts/art_direction_template.json campaigns/dreadmarsh/art_direction_template.json
cp src/prompts/master_beater_system.txt campaigns/dreadmarsh/master_beater_system.txt
cp src/prompts/master_beater_user.txt campaigns/dreadmarsh/master_beater_user.txt
cp src/prompts/scriptwriter_system.txt campaigns/dreadmarsh/scriptwriter_system.txt
cp src/prompts/scriptwriter_user.txt campaigns/dreadmarsh/scriptwriter_user.txt
cp src/prompts/style_integrator_system.txt campaigns/dreadmarsh/style_integrator_system.txt
cp src/prompts/style_integrator_user.txt campaigns/dreadmarsh/style_integrator_user.txt
cp src/prompts/entities_continuity_system.txt campaigns/dreadmarsh/entities_continuity_system.txt
cp src/prompts/entities_continuity_user.txt campaigns/dreadmarsh/entities_continuity_user.txt
cp src/prompts/page_prompt.txt campaigns/dreadmarsh/page_prompt.txt
```

Different campaigns can have completely different art styles:

```bash
mkdir -p campaigns/belowdown
cp src/prompts/art_direction_template.json campaigns/belowdown/art_direction_template.json
# Then edit campaigns/belowdown/art_direction_template.json for a different style.
```

## Running the pipeline

```bash
python src/pipeline.py <campaign> <SCRYBEQUILL_URL>
```

On the first run the episode folder is auto-named from the story title. Subsequent runs for the same URL create a new versioned subfolder, cloning the previous version as a baseline.

Each run also copies the effective art direction and prompt templates into the new version folder so the exact generation inputs are preserved alongside the checkpoints.

For generation mode, aspect ratio, and automated image generation, use the GUI (see [GUI](#gui)). The CLI currently runs the text pipeline through prompt generation.

### Examples

```bash
# First run — creates campaigns/dreadmarsh/dreadmarsh-crossing/v001/
python src/pipeline.py dreadmarsh https://scrybequill.com/share/...

# Re-run same episode — creates v002/ with all checkpoints cloned from v001 (no phases run)
python src/pipeline.py dreadmarsh https://scrybequill.com/share/...

# Select a different recap variant from cached scrape data
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --recap-version short

# Update story bible and everything downstream
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from beater

# Update art style integration only — creates v003/, clones v002/, re-runs Phase 4.5 and Phase 5
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from style

# Rebuild only the final page prompt from the styled script
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from prompt

# Skip style integration (Phase 4.5 becomes a no-op); Phase 5 reads from 03_script.json
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --skip-style

# Multi-page comic: 2 pages × 6 panels = 12 scenes in the story bible
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --total-pages 2 --panel-count 6

# Use alternate prompt templates for this run; copies them into the new version folder
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... \
  --rerun-from style \
  --scriptwriter-system-prompt custom_prompts/dreadmarsh_system.txt \
  --scriptwriter-user-prompt custom_prompts/dreadmarsh_user.txt \
  --style-integrator-system-prompt custom_prompts/dreadmarsh_style_system.txt \
  --style-integrator-user-prompt custom_prompts/dreadmarsh_style_user.txt \
  --page-prompt-template custom_prompts/dreadmarsh_page_prompt.txt

# Fix source text — creates v004/, clones v003/, re-runs everything from scrape
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from scrape

# Different campaign, same URL — completely isolated under campaigns/belowdown/
python src/pipeline.py belowdown https://scrybequill.com/share/...
```

### Optional flags

```
--campaigns-root PATH        default: campaigns/
--beater-model NAME          default: DEFAULT_MODEL (src/model_defaults.py)
--script-model NAME          default: DEFAULT_MODEL (src/model_defaults.py)
--style-model NAME           default: DEFAULT_MODEL (src/model_defaults.py)
--panel-count N              default: 6 (panels per page)
--total-pages N              default: 1 (number of comic pages)
--art-style-template PATH    Override campaign-level template for this run only
--master-beater-system-prompt PATH
                             Override the master beater system prompt template for this run only
--master-beater-user-prompt PATH
                             Override the master beater user prompt template for this run only
--scriptwriter-system-prompt PATH
                             Override the system prompt template for this run only
--scriptwriter-user-prompt PATH
                             Override the user prompt template for this run only
--style-integrator-system-prompt PATH
                             Override the style integrator system prompt template for this run only
--style-integrator-user-prompt PATH
                             Override the style integrator user prompt template for this run only
--page-prompt-template PATH  Override the page prompt template for this run only
--rerun-from PHASE           scrape | entities | beater | script | style | prompt
--recap-version VERSION      short | standard | alternate/alt | long
--skip-style                 Skip Phase 4.5 and generate Phase 5 prompt from 03_script.json
```

### Generation modes

The pipeline supports two image-prompt strategies:

| Mode | CLI / config value | Prompt output | Image output |
|---|---|---|---|
| **Page by Page** (default) | `page` | One prompt per page (`04_page_N_prompt.txt`) | One image per page (`05_page_N.png`) |
| **Panel by Panel** | `panel` | One prompt per panel (`04_page_N_panel_M_prompt.txt`) | One image per panel (`05_page_N_panel_M.png`), then stitched into a page (`06_page_N.png`) |

In **panel** mode:

- The scriptwriter runs once per panel (with per-panel story-bible checkpoints) and merges results into per-page script checkpoints.
- Character details in each panel prompt are filtered to characters referenced in that panel only.
- Panel prompts omit page-level elements (title, page number) that appear in page-mode prompts.
- After all panel images are generated, `image_stitcher.py` arranges them in a grid shaped by the configured aspect ratio and writes the composite page image.

Changing generation mode invalidates checkpoints from the script phase onward.

### Image generation

When `generate_images` is enabled (via the GUI **Generate images** checkbox or **Generate Images** button on the Output tab), the pipeline automatically:

1. Sends each `04_page_*_prompt.txt` file to the configured Gemini image model.
2. Saves the result as `05_page_*.png` (or `05_page_*_panel_*.png` in panel mode).
3. In panel mode, stitches panel images into `06_page_*.png`.

Image generation always uses Gemini via the OpenAI-compatible image API (`client.images.generate`). Configure the model in GUI Settings (default: `gemini-2.5-flash-image`). Prior versions of generated images are rotated to `_v1`, `_v2`, etc. when regenerated.

You can also generate images outside a full pipeline run from the Output tab: select a prompt file to regenerate a single image, use **Generate Images** to process all prompts in a version, or **Stitch Images** to rebuild composite pages from existing panel PNGs.

### Stage responsibilities

- Phase 1 scrapes the ScrybeQuill recap and caches all recap variants.
- Phase 2 extracts entities from scraped notes, then merges them with the campaign entities bible via an LLM continuity pass.
- Phase 3 master beater creates a story bible from beats (text-only scene breakdown). Total scene count = `panel_count × total_pages`.
- Phase 4 scriptwriter realizes the story bible into per-page script checkpoints with panel prose, dialogue, and continuity state. In panel mode, scripting runs per panel.
- Phase 4.5 style integrator rewrites only `setting` and `visual_action` on each page checkpoint.
- Phase 5 prompt generation produces image prompts from the styled script (or unstyled script when `--skip-style` is set).
- Phase 6 image generation (optional) sends prompts to Gemini and saves PNGs.
- Phase 7 stitching (panel mode only) combines panel PNGs into finished page images.

### Script generation behavior

- `--panel-count` and `--total-pages` together determine how many scenes the master beater produces.
- Scriptwriter follows the story bible text rather than deciding pacing from beats directly.
- Scraped quotes are included in model context as reference dialogue and used when scene-appropriate.

## Directory layout

```
campaigns/
  index.json                        # global lookup: campaign+URL → episode folder
  dreadmarsh/
    art_direction_template.json     # campaign-level art direction
    master_beater_system.txt        # campaign-level master beater system prompt
    master_beater_user.txt          # campaign-level master beater user prompt
    scriptwriter_system.txt         # campaign-level scriptwriter system prompt
    scriptwriter_user.txt           # campaign-level scriptwriter user prompt
    style_integrator_system.txt     # campaign-level style integrator system prompt
    style_integrator_user.txt       # campaign-level style integrator user prompt
    entities_continuity_system.txt  # campaign-level entities continuity system prompt
    entities_continuity_user.txt    # campaign-level entities continuity user prompt
    page_prompt.txt                 # campaign-level page prompt template
    entities_bible.json             # campaign-level merged entity continuity
    dreadmarsh-crossing/            # episode folder (slug from story title, identity from URL)
      episode_meta.json             # url, title, created_at
      v001/
        01_raw_text.json
        02_entities.json
        02_5_entities_bible.json            # version-local merged entity continuity
        02_5_story_bible.json
        02_6_story_bible_page_001.json          # per-page story bible slices
        03_script_page_001.json               # per-page script checkpoints
        03_5_styled_script_page_001.json        # per-page styled script checkpoints
        04_page_1_prompt.txt                    # page mode: one prompt per page
        04_page_1_panel_1_prompt.txt          # panel mode: one prompt per panel
        05_page_1.png                           # generated page image (page mode)
        05_page_1_panel_1.png                   # generated panel image (panel mode)
        06_page_1.png                           # stitched page image (panel mode)
        run_status.json                         # run outcome and errors
        art_direction_template.json
        master_beater_system.txt
        master_beater_user.txt
        scriptwriter_system.txt
        scriptwriter_user.txt
        style_integrator_system.txt
        style_integrator_user.txt
        entities_continuity_system.txt
        entities_continuity_user.txt
        page_prompt.txt
        prompts/                                # interpolated prompts sent to models
      v002/                         # second run; prior phases cloned, new phase re-run
        ...
  belowdown/
    art_direction_template.json
    ...
```

## Idempotency and version history

- Within a version, the pipeline skips any phase whose checkpoint already exists.
- A new version is created on every run (auto-incremented: v001, v002, ...).
- The previous version's files are cloned as a baseline so only phases invalidated by `--rerun-from` (or changed run settings like generation mode or panel count) are re-computed.
- The effective art direction and prompt template files are copied into every version folder for reproducibility.
- Episode identity is canonical by URL — if the story title changes on the source site, the same episode folder is reused.
- When `--skip-style` is set, Phase 4.5 is skipped and Phase 5 consumes `03_script_page_*.json` directly.
- Run settings (`panel_count`, `total_pages`, `aspect_ratio`, `generation_mode`, `generate_images`, etc.) are persisted per version in `run_status.json`.

## Running individual phases

The individual phase scripts accept explicit paths and are useful for debugging or one-off re-runs outside the pipeline.

**Phase 1 — Scrape**
```bash
python src/scraper.py <URL> --checkpoint campaigns/dreadmarsh/<episode>/v001/01_raw_text.json --recap-version standard
```

**Phase 2 — Entities**
```bash
python -c "from pathlib import Path; from entities import build_entities_from_raw; build_entities_from_raw(Path('campaigns/dreadmarsh/<episode>/v001/01_raw_text.json'), Path('campaigns/dreadmarsh/<episode>/v001/02_entities.json'))"
```

**Phase 3 — Master Beater**
```bash
python src/master_beater.py \
  --raw-input campaigns/dreadmarsh/<episode>/v001/01_raw_text.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --output campaigns/dreadmarsh/<episode>/v001/02_5_story_bible.json \
  --scene-count 6
```

**Phase 4 — Script**
```bash
python src/scriptwriter.py \
  --raw-input campaigns/dreadmarsh/<episode>/v001/01_raw_text.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --story-bible-input campaigns/dreadmarsh/<episode>/v001/02_6_story_bible_page_001.json \
  --output campaigns/dreadmarsh/<episode>/v001/03_script_page_001.json
```

**Phase 4.5 — Style Integration**
```bash
python src/style_integrator.py \
  --script-input campaigns/dreadmarsh/<episode>/v001/03_script_page_001.json \
  --art-style-template campaigns/dreadmarsh/art_direction_template.json \
  --output campaigns/dreadmarsh/<episode>/v001/03_5_styled_script_page_001.json
```

**Phase 5 — Prompt**
```bash
# Page mode (after style integration):
python src/prompter.py \
  --script-input campaigns/dreadmarsh/<episode>/v001/03_5_styled_script_page_001.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --art-style-template campaigns/dreadmarsh/art_direction_template.json \
  --output campaigns/dreadmarsh/<episode>/v001/04_page_1_prompt.txt

# Panel mode — use a single-panel script checkpoint and a panel prompt filename:
python src/prompter.py \
  --script-input campaigns/dreadmarsh/<episode>/v001/03_5_styled_script_page_001.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --art-style-template campaigns/dreadmarsh/art_direction_template.json \
  --output campaigns/dreadmarsh/<episode>/v001/04_page_1_panel_1_prompt.txt

# Skip-style flow (pipeline --skip-style):
python src/prompter.py \
  --script-input campaigns/dreadmarsh/<episode>/v001/03_script_page_001.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --art-style-template campaigns/dreadmarsh/art_direction_template.json \
  --output campaigns/dreadmarsh/<episode>/v001/04_page_1_prompt.txt
```

**Phase 6 — Image generation** (requires `GEMINI_API_KEY`)

```bash
python -c "
from pathlib import Path
from image_generator import ImageGenerator
prompt = Path('campaigns/dreadmarsh/<episode>/v001/04_page_1_prompt.txt')
out = Path('campaigns/dreadmarsh/<episode>/v001/05_page_1.png')
gen = ImageGenerator(model='gemini-2.5-flash-image')
gen.save_image(gen.generate_image(prompt.read_text()), out)
"
```

**Phase 7 — Stitching** (panel mode only)

```bash
python -c "
from pathlib import Path
from image_stitcher import stitch_panel_images
version = Path('campaigns/dreadmarsh/<episode>/v001')
panels = sorted(version.glob('05_page_1_panel_*.png'))
stitch_panel_images(panels, version / '06_page_1.png', aspect_ratio='3:2')
"
```

## Running tests

```bash
pytest
```

## Checkpoint files

| File | Contents |
|---|---|
| `01_raw_text.json` | Sanitized story text, title, author, cached recap variants |
| `02_entities.json` | Characters, locations, and story beats for this episode |
| `02_5_entities_bible.json` | Version-local copy of the merged campaign entities bible |
| `02_5_story_bible.json` | Full story bible with scene breakdown |
| `02_6_story_bible_page_NNN.json` | Per-page story bible slice used by the scriptwriter |
| `02_6_story_bible_page_NNN_panel_NNN.json` | Per-panel story bible slice (panel mode only) |
| `03_script_page_NNN.json` | Panelized comic script for one page with continuity fields |
| `03_script_page_NNN_panel_NNN.json` | Single-panel script checkpoint (panel mode only) |
| `03_5_styled_script_page_NNN.json` | Script checkpoint with art-direction-infused panel descriptions |
| `04_page_N_prompt.txt` | Composite image prompt for one multi-panel page (page mode) |
| `04_page_N_panel_M_prompt.txt` | Image prompt for a single panel (panel mode) |
| `05_page_N.png` | Generated page image (page mode) |
| `05_page_N_panel_M.png` | Generated panel image (panel mode) |
| `06_page_N.png` | Stitched composite page image (panel mode) |
| `run_status.json` | Run outcome, errors, and persisted run settings |
| `episode_meta.json` | Episode URL, display slug, creation timestamp |
| `campaigns/index.json` | Global campaign+URL → episode folder lookup |
| `entities_bible.json` | Campaign-level merged entity continuity across episodes |