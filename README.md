# TTRPG Comic Generator

Converts a ScrybeQuill session recap into a structured comic script via a campaign-aware, versioned, checkpoint-resumable pipeline.

Each run is isolated in its own version folder. Prior runs are never overwritten, so you can compare outputs across art style changes, text corrections, or model switches.

## Requirements

- Python 3.12+
- One of the following model backends configured for the model selected in `src/model_defaults.py`:
  - [Ollama](https://ollama.com) running locally for non-`gemini-` models
  - Google Gemini API access via `GEMINI_API_KEY` for models whose name starts with `gemini-`

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install playwright pydantic instructor ollama openai keyring pytest pytest-asyncio black
playwright install chromium
```

If you are using Gemini, set `GEMINI_API_KEY` in your environment or in a local `.env` file.

If you are using Ollama, make sure Ollama is running locally and the selected model is available. `OLLAMA_BASE_URL` defaults to `http://localhost:11434/v1`.

## Campaign setup

Each campaign has its own folder under `campaigns/`. On the first pipeline run, the campaign folder is bootstrapped automatically with reusable defaults for:

- `master_beater_system.txt`
- `master_beater_user.txt`
- `art_direction_template.json`
- `scriptwriter_system.txt`
- `scriptwriter_user.txt`
- `style_integrator_system.txt`
- `style_integrator_user.txt`
- `page_prompt.txt`

All five files are copied from the shared `prompts/` directory. Edit the campaign copies when you want campaign-specific behavior.

If you want to pre-seed defaults manually, copy them the same way:

```bash
mkdir -p campaigns/dreadmarsh
cp prompts/art_direction_template.json campaigns/dreadmarsh/art_direction_template.json
cp prompts/master_beater_system.txt campaigns/dreadmarsh/master_beater_system.txt
cp prompts/master_beater_user.txt campaigns/dreadmarsh/master_beater_user.txt
cp prompts/scriptwriter_system.txt campaigns/dreadmarsh/scriptwriter_system.txt
cp prompts/scriptwriter_user.txt campaigns/dreadmarsh/scriptwriter_user.txt
cp prompts/style_integrator_system.txt campaigns/dreadmarsh/style_integrator_system.txt
cp prompts/style_integrator_user.txt campaigns/dreadmarsh/style_integrator_user.txt
cp prompts/page_prompt.txt campaigns/dreadmarsh/page_prompt.txt
```

Different campaigns can have completely different art styles:

```bash
mkdir -p campaigns/belowdown
cp prompts/art_direction_template.json campaigns/belowdown/art_direction_template.json
# Then edit campaigns/belowdown/art_direction_template.json for a different style.
```

## Running the pipeline

```bash
python src/pipeline.py <campaign> <SCRYBEQUILL_URL>
```

On the first run the episode folder is auto-named from the story title. Subsequent runs for the same URL create a new versioned subfolder, cloning the previous version as a baseline.

Each run also copies the effective art direction and prompt templates into the new version folder so the exact generation inputs are preserved alongside the checkpoints.

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

# Skip style integration (Phase 3.5 becomes a no-op); Phase 4 reads from 03_script.json
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --skip-style

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
--scene-count N              default: 6 (target scene count for the story bible)
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

### Stage responsibilities

- Phase 2 extracts entities and canonical beats from the scraped recap.
- Phase 3 master beater creates a story bible from beats (text-only scene breakdown).
- Phase 4 scriptwriter realizes the story bible into final panel prose, dialogue, and continuity state.
- Phase 4.5 style integrator rewrites only `setting` and `visual_action`.
- Phase 5 page prompt generation still consumes the script checkpoint.

### Script generation behavior

- `--scene-count` targets the master beater stage.
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
    page_prompt.txt                 # campaign-level page prompt template
    dreadmarsh-crossing/            # episode folder (slug from story title, identity from URL)
      episode_meta.json             # url, title, created_at
      v001/
        01_raw_text.json
        02_entities.json
        02_5_story_bible.json
        03_script.json
        03_5_styled_script.json
        04_page_prompt.txt
        art_direction_template.json
        master_beater_system.txt
        master_beater_user.txt
        scriptwriter_system.txt
        scriptwriter_user.txt
        style_integrator_system.txt
        style_integrator_user.txt
        page_prompt.txt
      v002/                         # second run; prior phases cloned, new phase re-run
        ...
  belowdown/
    art_direction_template.json
    ...
```

## Idempotency and version history

- Within a version, the pipeline skips any phase whose checkpoint already exists.
- A new version is created on every run (auto-incremented: v001, v002, ...).
- The previous version's files are cloned as a baseline so only phases invalidated by `--rerun-from` are re-computed.
- The effective art direction and prompt template files are copied into every version folder for reproducibility.
- Episode identity is canonical by URL — if the story title changes on the source site, the same episode folder is reused.
- When `--skip-style` is set, Phase 4.5 is skipped and Phase 5 consumes `03_script.json` directly.

## Running individual phases

The individual phase scripts accept explicit paths and are useful for debugging or one-off re-runs outside the pipeline.

**Phase 1 — Scrape**
```bash
python src/scraper.py <URL> --checkpoint campaigns/dreadmarsh/<episode>/v001/01_raw_text.json --recap-version standard
```

**Phase 2 — Entities (deterministic from scraped notes)**
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
  --story-bible-input campaigns/dreadmarsh/<episode>/v001/02_5_story_bible.json \
  --output campaigns/dreadmarsh/<episode>/v001/03_script.json
```

**Phase 4.5 — Style Integration**
```bash
python src/style_integrator.py \
  --script-input campaigns/dreadmarsh/<episode>/v001/03_script.json \
  --art-style-template campaigns/dreadmarsh/art_direction_template.json \
  --output campaigns/dreadmarsh/<episode>/v001/03_5_styled_script.json
```

**Phase 5 — Prompt**
```bash
# Standard flow (after style integration):
python src/prompter.py \
  --script-input campaigns/dreadmarsh/<episode>/v001/03_5_styled_script.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --art-style-template campaigns/dreadmarsh/art_direction_template.json \
  --output campaigns/dreadmarsh/<episode>/v001/04_page_prompt.txt

# Skip-style flow (pipeline --skip-style):
python src/prompter.py \
  --script-input campaigns/dreadmarsh/<episode>/v001/03_script.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --art-style-template campaigns/dreadmarsh/art_direction_template.json \
  --output campaigns/dreadmarsh/<episode>/v001/04_page_prompt.txt
```

## Running tests

```bash
pytest
```

## Checkpoint files

| File | Contents |
|---|---|
| `01_raw_text.json` | Sanitized story text, title, author |
| `02_entities.json` | Characters, locations, story beats |
| `03_script.json` | Panelized comic script with continuity fields |
| `03_5_styled_script.json` | Script checkpoint with art-direction-infused panel descriptions |
| `04_page_prompt.txt` | Single composite image prompt for one multi-panel comic page |
| `episode_meta.json` | Episode URL, display slug, creation timestamp |
| `campaigns/index.json` | Global campaign+URL → episode folder lookup |
