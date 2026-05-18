## Pipeline & Core

- [ ] Add race & class to entities
- [ ] Add a second pass for continuity and fun
- [ ] character description merging and sync?

---

## GUI Implementation (see GUI_ROADMAP.md & GUI_DESIGN.md)

### Phase 0: Foundation — Pipeline Events & Config Model

- [x] Create `src/pipeline_config.py` with `RunConfig` dataclass
- [x] Create `src/pipeline_events.py` with `PipelineEvent` union and event types
- [x] Refactor `src/pipeline.py` to emit `PipelineEvent` instead of print()
- [x] Update CLI to consume events as presentation layer
- [x] Add event emission tests in `tests/test_pipeline.py`
- [x] Verify all existing pipeline tests still pass

### Phase 1: Services Layer

- [x] Create `src/repository_service.py` with RepositoryService class
  - [x] list_campaigns()
  - [x] list_episodes()
  - [x] latest_version()
  - [x] list_versions()
  - [x] get_version_files()
  - [x] get_campaign_prompts()
  - [x] get_version_prompts()
  - [x] run_status()
- [x] Create `src/settings_service.py` with SettingsService class
  - [x] get/set_gemini_api_key() (OS keyring)
  - [x] get/set_ollama_base_url()
  - [x] get/set_default_model()
  - [x] apply_to_environment()
- [x] Add `tests/test_repository_service.py` with functional tests
- [x] Add `tests/test_settings_service.py` with unit tests
- [x] Verify all existing tests still pass

### Phase 2: Run Controller & Event Streaming

- [x] Create `src/run_controller.py` with RunController class
  - [x] launch_run()
  - [x] current_run()
  - [x] cancel_run()
- [x] Add `tests/test_run_controller.py` with unit tests
- [x] Add integration test (RepositoryService + RunController)
- [x] Verify all tests pass

### Phase 3: Flet UI Shell & Navigation

- [x] Create `src/gui.py` main Flet application
- [x] Implement three-tab workspace navigation
- [x] Implement shared event log view
- [x] Implement Settings panel/dialog
  - [x] API key configuration
  - [x] Backend selection
  - [x] Default model selection
- [x] Add placeholder workspace pages (RunPage, PromptPage, OutputPage)
- [x] Add `tests/test_gui_integration.py` smoke tests
- [ ] Verify app starts without errors

### Phase 4: Run Workspace

- [ ] Implement RunPage component
  - [ ] Campaign dropdown (via RepositoryService)
  - [ ] Story URL text field
  - [ ] Rerun stage dropdown
  - [ ] Recap version dropdown
  - [ ] Skip style toggle
  - [ ] Panel count / total pages spinners
  - [ ] Model selectors (default or per-stage)
- [ ] Implement Run status panel
  - [ ] Status indicator
  - [ ] Current phase badge
  - [ ] Checkpoint readiness list
  - [ ] Latest version path display
- [ ] Implement Run button & disable logic
- [ ] State management for RunPage
- [ ] Implement event callback → UI update flow
- [ ] Add unit tests for RunPage state & button behavior
- [ ] Test end-to-end with mocked pipeline

### Phase 5: Prompt Workspace

- [ ] Implement PromptPage component
  - [ ] Campaign dropdown
  - [ ] Template file list (radio/clickable)
  - [ ] Load / Save / Reset buttons
- [ ] Implement text editor pane
- [ ] Implement art_direction_template.json validation
  - [ ] Required field checks (base_style, characters, color_palette, layout_and_composition, lettering_and_dialog, text_rendering_guide)
  - [ ] Show ✓/✗ feedback
  - [ ] Prevent save on invalid JSON
- [ ] Implement preview section (list of files to be captured)
- [ ] State management for PromptPage
- [ ] Add unit tests for validation & file operations
- [ ] Test save/load/reset flows with mock RepositoryService

### Phase 6: Output Workspace

- [ ] Implement OutputPage component
  - [ ] Campaign dropdown
  - [ ] Episode dropdown
  - [ ] Version dropdown (pre-select latest)
- [ ] Implement run status panel (parse run_status.json)
- [ ] Implement file browser list
- [ ] Implement preview pane
  - [ ] JSON pretty-printing
  - [ ] Text file display as-is
- [ ] Implement quick-action buttons
  - [ ] Copy to Clipboard
  - [ ] Open Version Folder
- [ ] State management for OutputPage
- [ ] Add unit tests for file listing & preview
- [ ] Test with mock RepositoryService

### Phase 7: Polish, Testing & Packaging

- [ ] Create comprehensive integration test suite
  - [ ] Multi-campaign temp setup
  - [ ] Launch run via GUI, verify output discovery
  - [ ] Edit campaign prompt, verify next run uses it
  - [ ] Error handling: missing campaign, invalid URL, API key not set
- [ ] Add graceful error handling across all workspaces
- [ ] Add user-facing error messages (not tracebacks) to event log
- [ ] Update README.md with GUI quickstart & screenshots
- [ ] Create GUI_TROUBLESHOOTING.md if needed
- [ ] Implement Windows packaging proof-of-concept
  - [ ] Verify Playwright/browser bundling strategy
  - [ ] Build Windows .exe locally or via Windows CI
  - [ ] Smoke test .exe with real campaign folder
  - [ ] Verify API keys are not visible in plain text
- [ ] Final integration test suite run (all tests pass)
- [ ] Document GUI usage & packaging notes in README.md

---

## Milestones

- [x] **Milestone 1** (End of Phase 1): Services are testable in isolation; CLI works
- [ ] **Milestone 2** (End of Phase 3): Flet app starts, tabs navigate, Settings dialog works
- [ ] **Milestone 3** (End of Phase 4): Run workspace is functional, user can launch mocked run
- [ ] **Milestone 4** (End of Phase 6): All workspaces feature-complete, full workflow usable
- [ ] **Milestone 5** (End of Phase 7): Windows .exe is produced, tested, and documented 
