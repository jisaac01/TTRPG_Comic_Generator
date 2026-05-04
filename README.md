# TTRPG Comic Generator

Converts a ScrybeQuill session recap into a structured comic script via a campaign-aware, versioned, checkpoint-resumable pipeline.

Each run is isolated in its own version folder. Prior runs are never overwritten, so you can compare outputs across art style changes, text corrections, or model switches.

## Requirements

- Python 3.12+
- [Ollama](https://ollama.com) running locally with `qwen2.5:7b` pulled

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install playwright pydantic instructor ollama openai pytest pytest-asyncio black
playwright install chromium
```

## Campaign setup

Each campaign has its own folder under `campaigns/`. Create a reusable art direction template for the campaign before the first run:

```bash
mkdir -p campaigns/dreadmarsh
cat > campaigns/dreadmarsh/art_direction_template.json << 'EOF'
{
  "base_style": "Brutalist, hand-inked graphic novel aesthetic. High contrast, Gothic shadows, heavy ink washes, grainy texture.",
  "color_palette": "Black and white only. No color.",
  "layout_and_composition": "One single comic page image containing all panels in order, with clear gutters and consistent character design across panels.",
  "lettering_and_dialog": "Lettering should feel hand-drawn, legible, and integrated with the page composition."
}
EOF
```

Different campaigns can have completely different art styles:

```bash
mkdir -p campaigns/belowdown
cat > campaigns/belowdown/art_direction_template.json << 'EOF'
{
  "base_style": "Watercolor fantasy illustration. Soft washes, warm tones, hand-lettered feeling.",
  "color_palette": "Full color with warm, natural hues.",
  "layout_and_composition": "One single comic page image containing all panels in order, with breathing room and soft panel borders.",
  "lettering_and_dialog": "Gentle hand-lettered dialogue with storybook clarity."
}
EOF
```

## Running the pipeline

```bash
python src/pipeline.py <campaign> <SCRYBEQUILL_URL>
```

On the first run the episode folder is auto-named from the story title. Subsequent runs for the same URL create a new versioned subfolder, cloning the previous version as a baseline.

### Examples

```bash
# First run — creates campaigns/dreadmarsh/dreadmarsh-crossing/v001/
python src/pipeline.py dreadmarsh https://scrybequill.com/share/...

# Re-run same episode — creates v002/ with all checkpoints cloned from v001 (no phases run)
python src/pipeline.py dreadmarsh https://scrybequill.com/share/...

# Update art style only — creates v003/, clones v002/, re-runs Phase 4 only
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from prompt

# Fix source text — creates v004/, clones v003/, re-runs everything from scrape
python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from scrape

# Different campaign, same URL — completely isolated under campaigns/belowdown/
python src/pipeline.py belowdown https://scrybequill.com/share/...
```

### Optional flags

```
--campaigns-root PATH        default: campaigns/
--analysis-model NAME        default: qwen2.5:7b
--script-model NAME          default: qwen2.5:7b
--panel-count N              default: 6
--art-style-template PATH    Override campaign-level template for this run only
--rerun-from PHASE           scrape | analyze | script | prompt
```

## Directory layout

```
campaigns/
  index.json                        # global lookup: campaign+URL → episode folder
  dreadmarsh/
    art_direction_template.json     # campaign-level art direction
    dreadmarsh-crossing/            # episode folder (slug from story title, identity from URL)
      episode_meta.json             # url, title, created_at
      v001/
        01_raw_text.json
        02_entities.json
        03_script.json
        04_page_prompt.txt
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
- Episode identity is canonical by URL — if the story title changes on the source site, the same episode folder is reused.

## Running individual phases

The individual phase scripts accept explicit paths and are useful for debugging or one-off re-runs outside the pipeline.

**Phase 1 — Scrape**
```bash
python src/scraper.py <URL> --checkpoint campaigns/dreadmarsh/<episode>/v001/01_raw_text.json
```

**Phase 2 — Analyze**
```bash
python src/analyzer.py \
  --input campaigns/dreadmarsh/<episode>/v001/01_raw_text.json \
  --output campaigns/dreadmarsh/<episode>/v001/02_entities.json
```

**Phase 3 — Script**
```bash
python src/scriptwriter.py \
  --raw-input campaigns/dreadmarsh/<episode>/v001/01_raw_text.json \
  --entities-input campaigns/dreadmarsh/<episode>/v001/02_entities.json \
  --output campaigns/dreadmarsh/<episode>/v001/03_script.json
```

**Phase 4 — Prompt**
```bash
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
| `04_page_prompt.txt` | Single composite image prompt for one multi-panel comic page |
| `episode_meta.json` | Episode URL, display slug, creation timestamp |
| `campaigns/index.json` | Global campaign+URL → episode folder lookup |
