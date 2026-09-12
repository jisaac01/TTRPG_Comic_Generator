## Pipeline & Core

- [x] figure out why the entities bible is created in the architect step and not in the entities step
- [x] Add a style selector (Run + Output; bundled + campaign `art_direction/`)
- [x] Add some descriptors like age, hair style, height, skin color, to physical description prompt
- [x] Fix bug where characters is blank
- [x] Add a new pipeline mode: refresh just the current stage (e.g. entities)
- [x] Add a "working copy" that is the version we'll be passing into the next pass
- [x] star versions as favorites
- [x] add a description to each run
- [ ] Script held-item continuity fails on first run (`held_items_before` / `held_items_after` missing or mismatched between panels). Validators already catch this in `scriptwriter._validate_item_continuity`; the run still lands as Partial with red GUI errors even when the rest of the pipeline is fine. Intended fix is the repair loop below — do not loosen the validator or hide the failure as the long-term answer.
- [ ] Continuity **repair loop** (after script, before style): bounded tool-using pass that *fixes* validator failures on the checkpoint. Give the model the error list and tools such as `read_script_page`, `write_panel_fields` (held-items / characters / dialogue as needed), `get_episode_entities`, `validate_continuity`. Loop until validators pass or N steps. Persist a `repair_trace.json` (tool calls + outcomes). This is mechanical: make `03_script_page_*.json` internally consistent so the run is not Partial. Not a rewrite-for-quality pass.
- [ ] **Second pass for continuity and fun** (critic rewrite, after a valid script): a separate LLM stage that reads the (repaired) script + recap/bible and rewrites for story flow, jokes, introductions, missing beats — the quality notes after the 22-page run. May still use the same validators as a gate, but the job is “make it better,” not “make held_items line up.” Do not collapse this into the repair loop.
- [ ] camera angle, panel lighting
- [ ] Allow rerun from specific version
- [ ] Style template editing UX beyond Prompts-tab multi-style list
- [ ] Add entities_bible to prompts page for editing
- [ ] Allow editing version files
- [ ] Add "professional" pipeline tooling, whatever that means, for the resume (repair loop above is the concrete item)
- [ ] Move character descriptions into the style step (so they are styled)
- [ ] *** Convert the prompt to the markdown style and try it ***
- [ ] Add a json/markdown mode to output everything in those formats
- [ ] Add a warning when prompt templates are older than the defaults, out of date
- [ ] Add "find and replace" function for fixing cascading errors through all version files
- [ ] Unify prompt audit capture (`render_and_capture` chokepoint; drop per-stage prepare I/O) — see [PLAN_prompt_audit.md](PLAN_prompt_audit.md)
- [ ] allowing edits in the output tab
- [ ] warning when the templates are out of date, 
- [ ] updating the brutalist style template to allow a splash of color, 
- [ ] adding functionality to easily regenerate the episode entities, 
- [ ] changing the version does not change the info displayed under the settings
- [ ] more visibility into what stage is running
- [ ] get rid of duplicate URLs inside campaign folders
- [ ] why does the campaign level index have episode level stuff? 
- [x] use Gemini API to discover image models
- [ ] bug: style setting reloads when changing the stage setting
- [x] Output un-styled prompts (replaces skip style): parallel `041_page_*_unstyled_prompt.txt` from the unstyled script
- [ ] Clean up user facing errors eg 2026-08-28 12:11:43 [Images] image_generation: page 1: Gemini generateContent failed (503) for gemini-3.1-flash-image: {
  "error": {
    "code": 503,
    "message": "This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.",
    "status": "UNAVAILABLE"
  }
}
- [ ] Try adding a section to the script for quotes that aren't interesting
- [ ] Add missing prompts to prompt edit tab (entities_continuity, entities_bible)
- [ ] Show the number of characters in the prompt
- [ ] Add PG-13 option
- [ ] Prompt with no style option
- [ ] "Chat mode"

Thoughts after full run of 22 page script:
Layout (possibly unfixable): 
Fonts different between pages
numbers in panels sometimes, sometimes not
gutter and margin spacing
titles on pages other than #1
panels left to right vs top to bottom on different pages

Prompt improvement: 
I'm wondering if a single style block at the beginning of the chat would work, then each prompt would be 2/3 as long and maybe have better results?
wondering if the 'cache busting' I did also busted character continuity inside the chat. Solution above would fix that.
Attempt to limit the number of characters on any page to some number, 3? Maybe this would reduce the amount of stuff the model has to pay attention to?

Script stuff:
script hallucination (maybe fixed by using a better script generation model - currently using Gemini 3.7 Flash - although I think maybe the story beats were 2.5 Flash so I could do it over ppphhhhhh)
script continuity & flow
character introductions

Story stuff: 
it seems like a lot of stuff is just missing from the story, and that's due to the scrybe summaries being incomplete. I could abandon those and feed in the recordings to get my own transcription/summaries with a lot more detail and completion.