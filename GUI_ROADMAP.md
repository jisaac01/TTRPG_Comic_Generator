# Flet GUI Implementation Roadmap

## Phase 0: Foundation — Pipeline Events & Config Model (Effort: 2-3 days)

**Goal**: Prepare the pipeline to be GUI-safe. Extract a structured run config, add event reporting, and keep the CLI as a presentation layer.

**Deliverables**:
- [x] New `src/pipeline_config.py`: `RunConfig` dataclass mirroring all real ComicPipeline options (campaign, url, campaigns_root, per-stage models, panel_count, total_pages, art/prompt template overrides, rerun_from, recap_version, skip_style).
- [x] New `src/pipeline_events.py`: `PipelineEvent` union (base class or enum) for `PhaseStart`, `PhaseSkip`, `PhaseWarning`, PhaseError`, `PhasePartialFailure`, `VersionCreated`, `RunCompleted`. Add event dataclass with timestamp, phase, message, exception payload where relevant.
- [x] Refactor `src/pipeline.py` `ComicPipeline.run()` to emit `PipelineEvent` instances instead of calling `print()` directly. Keep print output as a thin event-listener adapter so the CLI behavior is unchanged.
- [x] Add `run()` test coverage in `tests/test_pipeline.py` asserting event emission for skipped phases, warnings, and errors (extend existing mocks).
- [x] Update existing CLI in `src/pipeline.py` to receive and print events without changing the user-facing output.

**Success Criteria**:
- All existing `tests/test_pipeline.py` tests pass without modification.
- A new test verifies `ComicPipeline.run()` emits events in correct order for a full run and a rerun-from-style run.
- CLI output is behaviorally identical to baseline.

---

## Phase 1: Services Layer (Effort: 3-4 days)

**Goal**: Build the repository and settings abstractions so the GUI does not need to understand campaign/version/prompt filesystem conventions directly.

**Deliverables**:
- [x] New `src/repository_service.py`: `RepositoryService` class answering:
  - `list_campaigns() -> list[str]`: campaigns under campaigns_root.
  - `list_episodes(campaign: str) -> list[Episode]` (dataclass with campaign, slug, url, title, created_at from episode_meta.json).
  - `latest_version(campaign: str, episode_slug: str) -> str | None`: e.g. "v003".
  - `list_versions(campaign: str, episode_slug: str) -> list[VersionInfo]`: with status, errors, created_at from run_status.json if present.
  - `get_version_files(campaign: str, episode_slug: str, version: str) -> VersionFiles` (dataclass: raw_text, entities, script, styled_script, page_prompt paths + metadata).
  - `get_campaign_prompts(campaign: str) -> CampaignPrompts` (dataclass: paths to all 7 campaign-level prompt/template files).
  - `get_version_prompts(campaign: str, episode_slug: str, version: str) -> CampaignPrompts`: version-captured prompt paths.
  - `run_status(campaign: str, episode_slug: str, version: str) -> dict | None`: parse run_status.json.

- [x] New `src/settings_service.py`: `SettingsService` class for API configuration:
  - `get_gemini_api_key() -> str | None`: reads from OS keyring (use `keyring` library).
  - `set_gemini_api_key(key: str)`: writes to OS keyring.
  - `get_ollama_base_url() -> str`: defaults to `http://localhost:11434/v1`.
  - `set_ollama_base_url(url: str)`.
  - `get_default_model() -> str`: reads from persistent config file (~/.comic_generator_config.json or equivalent).
  - `set_default_model(model: str)`.
  - `apply_to_environment()`: writes GEMINI_API_KEY, OLLAMA_BASE_URL to os.environ so the existing llm_client contract is honored.

- [x] New `tests/test_repository_service.py`: functional tests creating temp campaign/episode/version directories and asserting discovery, listing, and file lookups.
- [x] New `tests/test_settings_service.py`: unit tests for settings read/write, keyring fallbacks, and environment application.

**Success Criteria**:
- `RepositoryService` correctly lists all campaigns and episodes in a multi-campaign temp setup.
- `SettingsService.apply_to_environment()` correctly populates os.environ before llm_client is invoked.
- Existing pipeline tests continue to pass.

---

## Phase 2: Run Controller & Event Streaming (Effort: 2-3 days)

**Goal**: Bridge the pipeline engine to the GUI with a controller that launches runs off-thread, emits events, and handles results.

**Deliverables**:
- [x] New `src/run_controller.py`: `RunController` class:
  - `launch_run(config: RunConfig, event_callback: Callable[[PipelineEvent], None])`: spawns the pipeline in an asyncio task, calls event_callback for each PipelineEvent, accumulates final result.
  - `current_run() -> RunInfo | None`: returns active run status or None if idle.
  - `cancel_run()`: requests cancellation (implementation depends on asyncio task model).
  - Internally tracks event history for this run, final status, version created.

- [x] New `tests/test_run_controller.py`: unit tests with a mock pipeline engine:
  - Launch a fake run, verify events are emitted in order.
  - Verify cancellation stops event emission.
  - Verify final result includes version_dir and error list.

- [x] Integration test combining `RepositoryService` + `RunController`: launch a run with a fake pipeline, then verify the created version is discoverable via the repository service.

**Success Criteria**:
- `RunController` successfully launches a run and emits a sequence of events without blocking the test thread.
- Events are reproducible: running the same config twice yields the same event sequence and version structure.
- Existing pipeline tests continue to pass.

---

## Phase 3: Flet UI Shell & Navigation (Effort: 2-3 days)

**Goal**: Create the main Flet window, routing, and workspace navigation structure without implementing the detailed workspace contents yet.

**Deliverables**:
- [x] New `src/gui.py`: main Flet application with:
  - `main()` function that builds and runs the app.
  - [x] Three-tab or sidebar navigation structure (Run, Prompt, Output workspaces).
  - [x] A top-level page that initializes RepositoryService, SettingsService, RunController.
  - [x] A shared event log view (vertical list of timestamped messages, auto-scrolling).
  - [x] A Settings panel/dialog accessible from a menu or gear icon.

- [x] Placeholder workspace pages: `RunPage`, `PromptPage`, `OutputPage` (flet.Container with a Text control saying "Coming next").

- [x] New `tests/test_gui_integration.py`: smoke test:
  - Instantiate main page with mocked services.
  - Navigate between tabs.
  - Verify Settings panel opens/closes.
  - Verify event log receives and displays a test event.

**Success Criteria**:
- App starts without errors when all dependencies are installed.
- Tabs/workspace navigation works.
- Settings panel is accessible and functional (can set/get API key without errors).
- Event log displays messages correctly.

---

## Phase 4: Run Workspace (Effort: 3-4 days)

**Goal**: Build the primary workflow surface for launching and monitoring runs.

**Deliverables**:
- [x] `RunPage` component: 
  - Campaign dropdown (populated by RepositoryService.list_campaigns()).
  - Story URL text field.
  - Rerun stage dropdown (scrape, entities, beater, script, style, prompt, or "Full run").
  - Recap version dropdown (short, standard, alternate, long).
  - Skip style toggle.
  - Panel count / total pages spinners.
  - Model selectors (dropdown for each: analysis, beater, script, style, or a single "all stages" selector with per-stage overrides hidden behind a toggle).
  - Run button, disabled while a run is active.
  - Current phase badge (e.g., "Running Phase 4: Writing script").
  - Run status summary (final: "✓ OK", "⚠ Partial", "✗ Failed").
  - Latest version path and link/button to open in file explorer or refresh Output tab.

- [x] State management:
  - `RunPage` holds the UI state (campaign, URL, options).
  - On Run button click, validates inputs and calls `RunController.launch_run()`.
  - Event callback updates phase badge and appends to shared event log.
  - On run completion, refreshes version discovery via RepositoryService and enables the Run button again.

- [x] Tests:
  - Mock RunController and verify Run button launches with correct config.
  - Verify phase badge updates as events arrive.
  - Verify Run button is disabled during a run and re-enabled on completion.

**Success Criteria**:
- A user can select a campaign, paste a URL, click Run, and see a realistic event log stream through to completion (with mocked pipeline).
- Latest version is discovered and displayed after run completion.
- Rerun workflow works: select an episode (URL pre-filled from latest version), change stage, click Run.

---

## Phase 5: Prompt Workspace (Effort: 2-3 days)

**Goal**: Build the campaign-level prompt and art-template editor.

**Deliverables**:
- [x] `PromptPage` component:
  - Campaign dropdown to select which campaign's prompts to edit.
  - List of 7 editable files: master_beater_system.txt, master_beater_user.txt, scriptwriter_system.txt, scriptwriter_user.txt, style_integrator_system.txt, style_integrator_user.txt, page_prompt.txt.
  - Art direction template JSON: special handling to validate required fields (base_style, characters, color_palette, layout_and_composition, lettering_and_dialog, text_rendering_guide) using rules from `src/prompter.py`.
  - File list with Load, Save, and Reset-to-Default buttons.
  - Large text area for editing.
  - Validation feedback: red border or error message if art template JSON is invalid.
  - Preview: show which files will be captured into the next run (all 7 + art template).

- [x] State management:
  - `PromptPage` fetches campaign prompts on campaign change via RepositoryService.
  - Load reads the file content into the text area.
  - Save writes the edited text back to disk.
  - Reset overwrites with the default from prompts/.
  - Art template validation uses `_load_art_template()` logic from `src/prompter.py`.

- [x] Tests:
  - Mock RepositoryService; verify file load/save flows.
  - Verify art template JSON validation rejects missing fields.
  - Verify Reset restores the default from prompts/.

**Success Criteria**:
- [x] User can edit a campaign prompt file and see changes persisted to disk.
- [x] User cannot save invalid art template JSON and receives clear error feedback.
- [x] Reset to default works correctly.

---

## Phase 6: Output Workspace (Effort: 2-3 days)

**Goal**: Browse and preview the latest versioned outputs.

**Deliverables**:
- [x] `OutputPage` component:
  - Campaign and episode selectors.
  - Version selector (dropdown of all versions, with latest pre-selected).
  - File browser: clickable list of checkpoint files present in the version (01_raw_text.json, 02_entities.json, 03_script.json, 03_5_styled_script.json, 04_page_prompt.txt, etc.).
  - Preview pane: show selected file content (JSON formatted/pretty-printed, text as-is).
  - Run status display: parse and display run_status.json (status, checkpoints, failed stages, errors/warnings as a list).
  - Quick-action buttons: Open Version Folder, Copy Latest Prompt Path, Copy Latest Script Path (for copy-paste into image generation tools).

- [x] State management:
  - `OutputPage` uses RepositoryService to list campaigns, episodes, versions, and files.
  - On version change, refresh the file list via `get_version_files()`.
  - On file click, load and display via a preview formatter.

- [x] Tests:
  - Mock RepositoryService; verify file list is correctly displayed for a given version.
  - Verify JSON preview is readable (not raw single-line).
  - Verify run_status errors/warnings are parsed and displayed.

**Success Criteria**:
- [x] User can browse a multi-version episode and see all outputs from any version.
- [x] Latest version is pre-selected and easily accessible.
- [x] Run errors and warnings are surfaced clearly so the user can diagnose failures.

---

## Phase 7: Polish, Testing & Packaging (Effort: 3-5 days)

**Goal**: Ensure the app is robust, well-tested, and ready to distribute.

**Deliverables**:
- [ ] Full integration test suite:
  - Create a realistic multi-campaign temp directory.
  - Launch a run via the GUI (mocked pipeline) and verify the output workspace discovers it.
  - Edit a campaign prompt and verify the next run uses the updated version.
  - Test error handling: invalid campaign, network timeout, API key missing, etc.

- [x] Error handling:
  - Graceful handling of missing campaigns_root, invalid URLs, API key not set, backend unreachable.
  - User-facing error messages (not tracebacks) in the event log.

- [ ] Documentation update:
  - Update [README.md](/Users/jisaac/src/TTRPG_Comic_Generator/README.md) with GUI quickstart, API key setup, screenshot/layout description.
  - Add [GUI_TROUBLESHOOTING.md](/Users/jisaac/src/TTRPG_Comic_Generator/GUI_TROUBLESHOOTING.md) if needed.

- [ ] Packaging proof-of-concept:
  - Build a Windows executable using `flet build windows` or equivalent on Windows CI.
  - Verify the .exe starts and can load the UI without errors.
  - Document Playwright/browser bundling strategy.

- [ ] Smoke test on Windows:
  - Test the .exe with a real campaign folder.
  - Verify settings (API key) persist and are not visible in plain text.
  - Verify a real run can be launched and monitored.

**Success Criteria**:
- All GUI integration tests pass.
- Windows .exe is produced and runs without errors.
- No secrets (API keys) are visible in shipped artifacts.
- Documentation includes GUI usage and packaging notes.

---

## Estimated Timeline

- **Phase 0**: 2–3 days (foundation, event layer, config model)
- **Phase 1**: 3–4 days (services: repository, settings)
- **Phase 2**: 2–3 days (run controller, threading)
- **Phase 3**: 2–3 days (Flet shell, navigation, settings panel)
- **Phase 4**: 3–4 days (Run workspace, primary workflow)
- **Phase 5**: 2–3 days (Prompt workspace, editing, validation)
- **Phase 6**: 2–3 days (Output workspace, browsing, errors)
- **Phase 7**: 3–5 days (integration tests, error handling, Windows packaging)

**Total**: ~20–28 days of focused development (4–6 weeks at part-time).

---

## Milestones

1. **Milestone 1 (End of Phase 1)**: Pipeline events and services are testable in isolation; CLI still works.
2. **Milestone 2 (End of Phase 3)**: Flet app starts, tabs navigate, Settings dialog works, event log receives messages.
3. **Milestone 3 (End of Phase 4)**: Run workspace is functional; user can launch a mocked run and see events stream.
4. **Milestone 4 (End of Phase 6)**: All three workspaces are feature-complete; full workflow is usable.
5. **Milestone 5 (End of Phase 7)**: Windows .exe is produced, tested, and documented.

---

## Dependencies

- `flet` (already installed)
- `keyring` (for OS secret storage)
- `pydantic` (already in use)
- `pytest`, `pytest-asyncio` (already in use)
- Windows CI or local Windows environment for Phase 7 packaging (cross-platform executable build is out of scope from macOS).

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Pipeline events add overhead or break CLI | Add event layer as thin wrapper; keep print as consumer of events |
| Services layer couples GUI tightly to filesystem | Use dataclass returns (RepositoryService, SettingsService); keep file I/O inside services only |
| Flet layout is complex or slow | Start with a simple 3-tab layout; avoid dynamic UI generation; test responsiveness early |
| Windows .exe is hard to distribute | Build on Windows, not macOS; verify Playwright bundling before release |
| API key leaks in logs or version outputs | Never write settings to campaign/version folders; use OS keyring; sanitize logs |

