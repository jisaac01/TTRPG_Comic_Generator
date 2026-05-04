# TTRPG Comic Generator

Converts a ScrybeQuill session recap into a structured comic script via a checkpoint-resumable pipeline.

## Requirements

- Python 3.12+
- [Ollama](https://ollama.com) running locally with `qwen2.5:7b` pulled

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install playwright pydantic instructor ollama openai pytest pytest-asyncio black
playwright install chromium
```

## Running the full pipeline

```bash
python src/pipeline.py <SCRYBEQUILL_URL>
```

The pipeline skips any phase whose checkpoint already exists under `checkpoints/`.

Optional flags:
```
--checkpoint-dir PATH   default: checkpoints
--analysis-model NAME   default: qwen2.5:7b
--script-model NAME     default: qwen2.5:7b
--panel-count N         default: 6
```

## Running individual phases

**Phase 1 — Scrape**
```bash
python src/scraper.py <URL> [--checkpoint checkpoints/01_raw_text.json] [--selector CSS]
```
Output: `checkpoints/01_raw_text.json`

**Phase 2 — Analyze**
```bash
python src/analyzer.py [--input checkpoints/01_raw_text.json] [--output checkpoints/02_entities.json] [--model qwen2.5:7b]
```
Output: `checkpoints/02_entities.json` — characters, locations, and scene-level beats with attributed quotes.

**Phase 3 — Script**
```bash
python src/scriptwriter.py [--raw-input checkpoints/01_raw_text.json] [--entities-input checkpoints/02_entities.json] [--output checkpoints/03_script.json] [--model qwen2.5:7b] [--panel-count 6]
```
Output: `checkpoints/03_script.json` — continuity-aware panel script with setting, visual action, dialogue overlays, and held-item transitions.

## Running tests

```bash
pytest
```

## Checkpoints

| File | Contents |
|---|---|
| `01_raw_text.json` | Sanitized story text, title, author |
| `02_entities.json` | Characters, locations, story beats |
| `03_script.json` | Panelized comic script with continuity fields |

Delete a checkpoint file to force that phase to re-run.
