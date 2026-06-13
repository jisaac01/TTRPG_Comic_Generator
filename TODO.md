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
  - The real continuity merge needs an LLM-assisted synthesis step that compares:
    1. the current episode’s extracted entities (`02_entities.json`),
    2. the existing campaign-root `entities_bible.json`, and
    3. the current episode text where names/aliases appear.
  - The model should propose a canonical campaign bible entry for each character/NPC, including the best available description, class, race, aliases, and physical notes.
  - Exact-name matches can be handled deterministically, but contradictory or fuzzy cases need model judgment and warnings.

- [ ] Step 3: Implement the campaign-root entities bible stage
  - Create or update `campaigns/<campaign>/entities_bible.json`.
  - Keep `02_entities.json` as the episode-local snapshot.
  - Copy the campaign bible into the current episode version directory for downstream use.
  - Emit warnings for contradictory descriptions, ambiguous names, and alias collisions.

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

