---
name: combine-episodes
description: >-
  Combine multiple 01_raw_text.json episode scrapes into one checkpoint and
  bootstrap a new campaign folder (art style + working/ recap) so the pipeline
  can run without scraping. Use when the user wants to merge recaps, combine
  FLAIL/ScrybeQuill episodes, create a combined campaign, or runs
  /combine-episodes.
argument-hint: "<raw-json...> --campaign NAME [--from-campaign NAME]"
---

# Combine episodes

Turn N episode `01_raw_text.json` files into one checkpoint and a runnable campaign tree. Do not change pipeline code.

## Inputs

Collect before writing anything. Ask only for what is missing.

| Input | Required | Notes |
| --- | --- | --- |
| Episode JSON files, in story order | yes | `01_raw_text.json` / `RawTextCheckpoint` |
| New campaign name | yes | folder name, e.g. `flail_combined` |
| Combined title | no | default: strip `Pt N` from the first title |
| Source campaign for art style | no | default: campaign named in the files, or ask |
| Write location | no | try live campaigns root first (below) |

## Step 1 — Inspect

Run the helper (paths relative to this skill directory):

```bash
python3 .grok/skills/combine-episodes/scripts/inspect_raw_texts.py \
  <file1.json> <file2.json> ... \
  --title "<combined title>" \
  --url "https://no-scrape.invalid/<campaign>" \
  --skeleton /tmp/combined_skeleton.json
```

Read the overlap report. Same normalized name (`strip`, collapse space, lowercase) is one identity.

## Step 2 — Merge the checkpoint

Write one `01_raw_text.json` matching `RawTextCheckpoint` in `src/scraper.py`.

**Concatenate** (skeleton already does this):

- `content` — each episode's `content`, prefixed with that episode's `title`
- each `recap_variants` key independently (`standard`, `long`, `short`, `alternate`)
- `quotes` — one array, skip exact `text`+`attribution` duplicates
- `outline` — episode order, unchanged (beat headings stay `### `)

**Do not concatenate entity lists.** `entities._dedupe_by_name` keeps the **first** record per normalized name and drops the rest. Duplicate keys lose later-episode detail.

For `player_characters`, `npcs`, `items`, `locations`, `factions`, `quests`:

1. Group by normalized name.
2. Also fold aliases into one record when they are clearly the same person/place (spelling variants, `"Merelda"` vs `"Merelda the Witch"`, `"Donkey"` later named `"Butterscotch"`). Put the canonical name in `name` and mention other names in the description.
3. Write **one** description that covers identity and what changed across episodes. Do not paste the four blurbs end to end.
4. Keep one-off names as-is.
5. PCs take priority: a name in `player_characters` must not also appear in `npcs`.

Other fields:

- `url`: dummy, non-empty (pipeline `RunConfig` rejects blank URLs). Use `https://no-scrape.invalid/<campaign>` unless the user supplies one. Never use `scrybequill.com` — a mistaken scrape would hit the real site. `.invalid` is a reserved TLD and will not resolve.
- `selected_recap`: `standard` unless asked otherwise
- `source_selector`: copy from the inputs
- `scraped_at`: last episode's timestamp (or now)
- `author`: `null` if unknown

Validate:

```bash
.venv/bin/python -c "
from pathlib import Path
import sys
sys.path.insert(0, 'src')
from scraper import RawTextCheckpoint
p = Path('<combined.json>')
raw = RawTextCheckpoint.model_validate_json(p.read_text())
print('ok', raw.title, 'pcs', [c.name for c in raw.player_characters], 'npcs', [c.name for c in raw.npcs])
"
```

Every entity name in a field must be unique after `_normalize_name`. If two records would collapse, merge them before writing.

## Step 3 — Bootstrap the campaign

Copy **style only**. Do not copy prompt templates (pipeline bootstraps missing ones). Do not copy `entities_bible.json` or old `vNNN` folders.

Campaign root, in order:

1. `app_paths.default_campaigns_root()` (live app-data, or `COMIC_GENERATOR_CAMPAIGNS_ROOT`)
2. If that write is denied (sandbox), write under repo `campaigns/` and tell the user to copy it into app-data

Layout:

```
<campaigns-root>/<campaign>/
  art_direction/<stem>.json
  <episode-slug>/
    episode_meta.json
    working/01_raw_text.json
```

- **Episode slug**: same as `pipeline._slugify(title)` (`lower`, non-word → space, whitespace → `-`).
- **Art style**: from the source campaign, last `art_direction_template.json` (newest `vNNN`, else campaign root). If the source already has `art_direction/*.json`, copy those instead. Stem: hyphenated lowercase from the first clause of `base_style`, or keep the existing filename.
- **`working/01_raw_text.json`**: the merged checkpoint. No `v001`.
- **`episode_meta.json`**: `{url, slug, title, created_at}` (UTC ISO). `url` must match the checkpoint `url`.
- **Index**: add `"<campaign>::<url>": "<slug>"` to `<campaigns-root>/index.json`. Do not change other keys.

## Step 4 — Tell the user how to run

- GUI: campaign `<name>`, **Existing Episode** (not Story URL), recap `standard`. Next run clones `working/` → `v001` and skips scrape.
- If the tree landed in the repo, they must copy `<campaign>/` into the live campaigns root and add the same index key.
- Do not scrape the dummy URL. Rerun-from scrape will fail on purpose.