## Pipeline & Core

- [ ] Campaign level entity caching
- [ ] Add race & class to entities
- [ ] Add a second pass for continuity and fun
- [ ] character description merging and sync?
- [ ] remove validation (e.g. carried items)
- [ ] panel stitching
---

### Entities bible / continuity plan

- [x] Step 1: Add continuity metadata to entities and update consumers
  - Added optional character fields for `class`, `race`, `physical_description`, and `aliases` in [src/entities.py](src/entities.py).
  - Updated prompt consumers in [src/master_beater.py](src/master_beater.py), [src/scriptwriter.py](src/scriptwriter.py), and [src/prompter.py](src/prompter.py) to render the richer character context.
  - Added tests for the richer schema in [tests/test_entities.py](tests/test_entities.py).

- [x] Step 2: Define what “merge” really means
  - This is not a simple file union or deterministic dedupe pass.
  - The current deterministic helper in [src/entities.py](src/entities.py) only handles exact-name matches, richer-description retention, alias union, and ambiguity warnings.
  - The current “pick the longer description” rule is a conservative placeholder fallback; it is not the final LLM-backed continuity merge.
  - The real continuity merge still needs an LLM-assisted synthesis step that compares:
    1. the current episode’s extracted entities (`02_entities.json`),
    2. the existing campaign-root `entities_bible.json`, and
    3. the current episode text where names/aliases appear.
  - The model should propose a canonical campaign bible entry for each character/NPC, including the best available description, class, race, aliases, and physical notes.
  - Exact-name matches can be handled deterministically, but contradictory or fuzzy cases need model judgment and warnings.

- [x] Step 3: Implement the campaign-root entities bible stage
  - Create or update `campaigns/<campaign>/entities_bible.json`.
  - Add a version-local copy as `02_5_entities_bible.json` for downstream consumers.
  - Keep `02_entities.json` as the episode-local snapshot.
  - Copy the campaign bible into the current episode version directory for downstream use.
  - Emit warnings for contradictory descriptions, ambiguous names, and alias collisions.

- [x] Step 3.1: Implement the explicit bible source-precedence algorithm
  - Use the existing campaign-root `entities_bible.json` first when it already exists.
  - If not, use the previous version’s `02_entities.json` in the current episode directory.
  - If there is no previous version in the current episode, use the latest `02_entities.json` from the most recently created previous episode in the same campaign.
  - If neither history source exists, fall back to the current version’s `02_entities.json`.
  - Merge the chosen source with the current version using the current deterministic helper, and keep this as the placeholder until the later LLM continuity merge is added.

- [ ] Step 4: Use the bible in downstream generation
  - Feed the merged bible into the master-beater/story-bible path.
  - Add conservative alias-based name normalization before the master beater sees the raw text.
  - Keep the existing episode-local entities file for traceability, but make the downstream consumers use the richer bible-backed entity context.

- [ ] Step 5: Add tests for merge behavior and warnings
  - Merge exact-name and alias matches.
  - Detect contradictory descriptions and near-duplicate names.
  - Verify alias replacement is conservative (e.g. “Wulf” / “Wolf” without corrupting “Sea Wolf”).
  - Verify the pipeline creates the campaign bible and version-local copy.

### Notes
- I now believe the merge step should be a small LLM-backed continuity pass, not just a mechanical JSON merge.
- The deterministic part is still useful for exact-name normalization and fallback behavior, but the real conflict resolution will need model reasoning.
- The current step 1 implementation is complete; the remaining work is the actual bible merge stage and its validation/warning path.
- Important clarification: the new `class`, `race`, and `physical_description` fields are currently carried by the entity model but are not yet populated by the raw scraper path. Those values should come from future source extraction or an LLM continuity pass, not from the current scrape output.

