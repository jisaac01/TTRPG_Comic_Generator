# Flet GUI Layout & Navigation Design

## Overall Structure

```
┌─────────────────────────────────────────────────────────────────┐
│  TTRPG Comic Generator GUI                              [_][□][X] │
├─────────────────────────────────────────────────────────────────┤
│  ☰  Run | Prompts | Output                   ⚙️ Settings        │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  [Workspace Content]                                             │
│                                                                   │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│  Event Log (shared across all workspaces)                        │
│  ─────────────────────────────────────────────────────────────   │
│  2026-05-18 14:32:05  [Run] Phase 1 skipped (checkpoint exists) │
│  2026-05-18 14:32:06  [Run] Phase 2: Building entities...       │
│  2026-05-18 14:32:15  [Run] Phase 2 done.                       │
│  ...                                                              │
└─────────────────────────────────────────────────────────────────┘
```

**Top Navigation**: Three workspace tabs (Run, Prompts, Output) + Settings gear icon in top right.

**Event Log**: Shared across all workspaces, at the bottom, auto-scrolling, max 100 lines retained.

**Status Bar**: Optional right-aligned indicator (e.g., "✓ v003 ready" or "🔄 Running Phase 4").

---

## Run Workspace

Primary workflow surface for launching and monitoring runs.

```
┌─────────────────────────────────────────────────────────────────┐
│  RUN WORKSPACE                                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Campaign:  [dreadmarsh ▼]  Story URL:  [https://scrybequill... │
│                                                                   │
│  ┌─── Run Configuration ────────────────────────────────────┐   │
│  │  Rerun from:  [Full run ▼]  Recap variant: [standard ▼]│   │
│  │  ☐ Skip style integration                                │   │
│  │                                                           │   │
│  │  Panel count: [6    ]   Total pages: [1    ]             │   │
│  │                                                           │   │
│  │  Model settings:                                         │   │
│  │    ○ Use default (gemini-3.1-flash-lite)                │   │
│  │    ○ Per-stage overrides ⌄                              │   │
│  │                                                           │   │
│  │    [If per-stage selected:]                              │   │
│  │    Story Bible:    [gemini-3.1-flash-lite ▼]            │   │
│  │    Scriptwriter:   [gemini-3.1-flash-lite ▼]            │   │
│  │    Style:         [gemini-3.1-flash-lite ▼]            │   │
│  │                                                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── Run Status ──────────────────────────────────────────┐   │
│  │  Status: ○ Idle  ⚠ Running  ✓ Complete  ✗ Error         │   │
│  │  Current phase: [4: Writing script]      Progress: ▓▓░░ │   │
│  │  Latest version: campaigns/dreadmarsh/dreadmarsh-crossing/v003 │   │
│  │                                       [Open folder]       │   │
│  │                                                           │   │
│  │  ✓ 01_raw_text.json          ✓ 03_script.json           │   │
│  │  ✓ 02_entities.json          ✓ 03_5_styled_script.json  │   │
│  │  ✓ 02_5_story_bible.json     ✓ 04_page_1_prompt.txt     │   │
│  │                                                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│            [Run] (disabled while running)                        │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│  Event Log                                                       │
│  ─────────────────────────────────────────────────────────────   │
│  [scrollable list of timestamped events]                        │
└─────────────────────────────────────────────────────────────────┘
```

**Components**:

1. **Campaign & URL Row** (top):
   - Campaign dropdown: populated by `RepositoryService.list_campaigns()`.
   - Story URL text field: accepts ScrybeQuill URLs.

2. **Run Configuration Panel** (collapsible or always visible):
   - **Rerun stage**: Dropdown with "Full run" (default), "scrape", "entities", "beater", "script", "style", "prompt".
   - **Recap variant**: Dropdown (short, standard, alternate, long).
   - **Skip style**: Toggle checkbox.
   - **Panel count / Total pages**: Spinner controls.
   - **Model settings**: 
     - Default/Per-stage radio buttons.
     - If per-stage selected, three dropdowns for beater, script, style models.

3. **Run Status Panel** (always visible):
   - Status indicator: idle/running/complete/error (icon + text).
   - Current phase badge with progress bar.
   - Latest version path with "Open folder" button.
   - Checkpoint readiness list (✓/✗ for each phase output).

4. **Run Button** (disabled while running).

**Behavior**:
- On campaign change, pre-fill the Story URL if an episode exists in that campaign (optional UX enhancement).
- On Run click, validate (campaign + URL not empty), then call `RunController.launch_run()` with a RunConfig built from UI state.
- Event callback updates phase badge, progress bar, checkpoint list, and appends to event log.
- On run completion, refresh latest version, enable Run button, and optionally switch to Output tab to show results.

---

## Prompts Workspace

Edit campaign-level prompts and art direction templates.

```
┌─────────────────────────────────────────────────────────────────┐
│  PROMPTS WORKSPACE                                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Campaign: [dreadmarsh ▼]                                        │
│                                                                   │
│  ┌─── Template Files ──────────────────────────────────────┐   │
│  │  ○ master_beater_system.txt                              │   │
│  │  ○ master_beater_user.txt                                │   │
│  │  ○ scriptwriter_system.txt                               │   │
│  │  ○ scriptwriter_user.txt                                 │   │
│  │  ○ style_integrator_system.txt                           │   │
│  │  ○ style_integrator_user.txt                             │   │
│  │  ○ page_prompt.txt                                        │   │
│  │  ○ art_direction_template.json  (requires validation)    │   │
│  │                                                           │   │
│  │  [Load]  [Save]  [Reset to Default]                     │   │
│  │                                                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── Editor ──────────────────────────────────────────────┐   │
│  │  [file: scriptwriter_system.txt]                         │   │
│  │  ────────────────────────────────────────────────────   │   │
│  │  You are a scriptwriter. Given a story bible page,      │   │
│  │  generate a single-page comic script with {panel_count} │   │
│  │  panels. Each panel must have:                          │   │
│  │  - setting: one-line scene description                 │   │
│  │  - visual_action: what the artist should draw           │   │
│  │  - dialogue_overlay: list of dialogue lines             │   │
│  │  ...                                                      │   │
│  │  ────────────────────────────────────────────────────   │   │
│  │  ✓ File saved. Ready for next run.                      │   │
│  │                                                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ✓ Art direction template is valid (6/6 required fields).      │
│    Next run will capture: campaigns/dreadmarsh/master_beater... │
│                          campaigns/dreadmarsh/scriptwriter...   │
│                          ... (all 8 files listed)              │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│  Event Log                                                       │
│  ─────────────────────────────────────────────────────────────   │
│  [scrollable list]                                              │
└─────────────────────────────────────────────────────────────────┘
```

**Components**:

1. **Campaign dropdown** (top): Select which campaign's prompts to edit.

2. **Template file list** (radio buttons or clickable list):
   - 7 text files + 1 JSON file (art direction).
   - Clicking a file loads it into the editor.

3. **Load / Save / Reset buttons**:
   - Load: fetch from disk via RepositoryService.
   - Save: write to disk.
   - Reset: restore from prompts/ defaults.

4. **Editor pane**:
   - Large text area for editing.
   - File name indicator at the top.

5. **Validation feedback**:
   - For art_direction_template.json: validate required fields (base_style, characters, color_palette, layout_and_composition, lettering_and_dialog, text_rendering_guide).
   - Show ✓ or ✗ with field names if invalid.
   - Prevent Save if JSON is invalid.

6. **Preview section** (below editor):
   - List of files that will be captured into the next version.
   - Clarify that historical versions' prompts are read-only.

**Behavior**:
- On campaign change, reset editor to empty state.
- On file click, load content via RepositoryService.
- On Save, write to disk and show confirmation.
- Art direction JSON validation happens on Save and on art_direction_template.json load.
- If validation fails, show clear error and do not allow save.

---

## Output Workspace

Browse and preview versioned outputs.

```
┌─────────────────────────────────────────────────────────────────┐
│  OUTPUT WORKSPACE                                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Campaign: [dreadmarsh ▼]  Episode: [dreadmarsh-crossing ▼]    │
│  Version: [v003 (latest) ▼]                                     │
│                                                                   │
│  ┌─── Run Status ──────────────────────────────────────────┐   │
│  │  Status: ✓ ok                                            │   │
│  │  Checkpoints: raw_text, entities, story_bible, script,  │   │
│  │               styled_script, page_prompt                │   │
│  │  No errors.                                              │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── Files ───────────────────────────────────────────────┐   │
│  │  ✓ 01_raw_text.json             (scraped content)      │   │
│  │  ✓ 02_entities.json             (characters, places)   │   │
│  │  ✓ 02_5_story_bible.json        (scene breakdown)      │   │
│  │  ✓ 03_script.json               (panels + dialogue)    │   │
│  │  ✓ 03_5_styled_script.json      (art-integrated)      │   │
│  │  ✓ 04_page_1_prompt.txt         (image generation)    │   │
│  │  📁 prompts/                     (captured prompts)     │   │
│  │     - master_beater_system_FINAL.txt                    │   │
│  │     - scriptwriter_system_FINAL_page_001.txt            │   │
│  │     ...                                                  │   │
│  │  ✓ run_status.json              (metadata)              │   │
│  │                                                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── Preview ─────────────────────────────────────────────┐   │
│  │  [File: 03_script.json (click to change)]               │   │
│  │  ────────────────────────────────────────────────────   │   │
│  │  {                                                        │   │
│  │    "url": "https://...",                                │   │
│  │    "title": "Dreadmarsh Crossing",                      │   │
│  │    "panel_count": 2,                                     │   │
│  │    "panels": [                                           │   │
│  │      {                                                    │   │
│  │        "index": 1,                                       │   │
│  │        "setting": "Swamp edge at dusk",                 │   │
│  │        "visual_action": "Del raises a torch...",        │   │
│  │        ...                                               │   │
│  │      }                                                    │   │
│  │    ]                                                      │   │
│  │  }                                                        │   │
│  │  ────────────────────────────────────────────────────   │   │
│  │                                                           │   │
│  │  [Copy to Clipboard]  [Open Version Folder]             │   │
│  │                                                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│  Event Log                                                       │
│  ─────────────────────────────────────────────────────────────   │
│  [scrollable list]                                              │
└─────────────────────────────────────────────────────────────────┘
```

**Components**:

1. **Navigation row** (top):
   - Campaign dropdown.
   - Episode dropdown (populated based on campaign).
   - Version dropdown (populated based on episode, with latest pre-selected).

2. **Run Status panel**:
   - Display parsed run_status.json: status, completed checkpoints, errors/warnings as a bullet list.

3. **Files list**:
   - Clickable file list showing all outputs in the version.
   - ✓ for present files, ✗ for missing.
   - Nested folder indicator for `prompts/` subdirectory.

4. **Preview pane**:
   - Shows selected file content.
   - JSON files are pretty-printed.
   - Text files are displayed as-is.
   - File name indicator at the top.

5. **Quick-action buttons**:
   - Copy to Clipboard (for latest page_prompt.txt, to paste into image generator).
   - Open Version Folder (open file explorer at version path).

**Behavior**:
- On campaign/episode change, refresh available versions.
- Latest version is pre-selected.
- On file click, load and display in preview.
- If a required checkpoint is missing, surface that clearly in the status panel.
- Errors and warnings from run_status.json are parsed and displayed as a readable list.

---

## Settings Panel / Dialog

Accessible from the ⚙️ icon in the top-right corner.

```
┌─────────────────────────────────────────────────────────────────┐
│  Settings                                                [_][X]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─── API Configuration ──────────────────────────────────┐   │
│  │  Model Backend:                                         │   │
│  │    ○ Gemini (Google)                                    │   │
│  │    ○ Ollama (Local)                                     │   │
│  │                                                          │   │
│  │  Gemini API Key:                                        │   │
│  │    [••••••••••••••••••••••••] (hidden, OS keyring)      │   │
│  │    [Change...]                                          │   │
│  │                                                          │   │
│  │  Ollama Base URL:                                       │   │
│  │    [http://localhost:11434/v1    ]                     │   │
│  │                                                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── Defaults ────────────────────────────────────────────┐   │
│  │  Default Model:                                         │   │
│  │    [gemini-3.1-flash-lite ▼]                            │   │
│  │                                                          │   │
│  │  Default Campaigns Root:                                │   │
│  │    [/Users/jisaac/src/TTRPG_Comic_Generator/campaigns] │   │
│  │    [Browse...]                                          │   │
│  │                                                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── Application ─────────────────────────────────────────┐   │
│  │  ☐ Start with last campaign                             │   │
│  │  ☐ Auto-refresh outputs on run completion              │   │
│  │  Theme: [Light ▼]                                       │   │
│  │                                                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│                                   [Cancel]  [Save Settings]     │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

**Components**:

1. **API Configuration**:
   - Radio buttons for backend choice (Gemini vs. Ollama).
   - Gemini API Key field (hidden, reads/writes via OS keyring).
   - Ollama Base URL field (with default pre-filled).

2. **Defaults**:
   - Default Model dropdown (matches what's in the Run workspace).
   - Campaigns Root picker (directory selector).

3. **Application settings** (optional for MVP):
   - Toggle for auto-refresh on run completion.
   - Theme selector (Light/Dark, if Flet supports it).

4. **Buttons**: Cancel, Save Settings.

**Behavior**:
- On open, load current settings from SettingsService.
- On Save, write settings back via SettingsService.
- SettingsService.apply_to_environment() is called so the next run uses the new settings.
- Gemini API Key is written to OS keyring, not to disk.

---

## Event Log (Shared)

Appears at the bottom of all workspaces. Scrollable, auto-scrolling on new events, max 100 lines.

**Format**:
```
TIMESTAMP  [CATEGORY]  MESSAGE
2026-05-18 14:32:05  [Run]    Phase 1 skipped (checkpoint exists)
2026-05-18 14:32:06  [Run]    Phase 2: Building entities...
2026-05-18 14:32:15  [Run]    Phase 2 done.
2026-05-18 14:32:16  [Warn]   Style integration partially failed on page 1: ...
2026-05-18 14:32:20  [Run]    Phase 5 done.
2026-05-18 14:32:20  [Status] Run completed. Version: v003. Status: partial (1 error).
```

**Categories**:
- `[Run]`: Normal progress.
- `[Warn]`: Warnings (partial failures, recoverable errors).
- `[Error]`: Errors (phase failed completely).
- `[Status]`: Final run status.
- `[File]`: Prompt/file operations (save, load, reset).

**Scrolling behavior**:
- Auto-scroll to new messages.
- User can scroll up to review history.
- Clear button to reset log (optional).

---

## Navigation & State Flow

```
User opens app
    ↓
Settings Panel?
    ├─→ Yes: Configure API key, backend, defaults
    │         ↓
    │    Save & return to Run tab
    │
    └─→ No: Go to Run tab

Run Tab
    ├─→ Select campaign + URL
    │   ├─→ Rerun? Or full run?
    │   ├─→ Edit options
    │   ├─→ Click Run
    │   └─→ Monitor event log
    │
    └─→ Run completes
        ├─→ Auto-switch to Output tab (optional)
        └─→ Display results & errors

Prompts Tab
    ├─→ Select campaign
    ├─→ Edit prompts/art direction
    ├─→ Save to disk
    └─→ Return to Run tab for next run

Output Tab
    ├─→ Select campaign/episode/version
    ├─→ Browse files
    ├─→ Preview outputs
    ├─→ Copy paths or open folder
    └─→ Return to Run tab to rerun from a stage
```

---

## Key Interaction Patterns

1. **Frequent Workflow** (iterate & reruns):
   - Run tab → configure → Run → (event log streams) → Output tab → review errors → Prompts tab → edit → Run tab → rerun from style/prompt stage → repeat.

2. **Rare Workflow** (setup):
   - Settings panel → enter API key → Save → go to Run tab.

3. **Debugging Workflow**:
   - Output tab → select version → browse run_status.json → read errors → Prompts tab → edit template → Run tab → rerun from stage.

4. **Comparison Workflow** (optional, future):
   - Output tab → select two versions → side-by-side file preview.

---

## Accessibility & Polish

- Large, readable text sizes.
- Color-coded status (green for ✓, yellow for ⚠, red for ✗).
- Disabled state for buttons (e.g., Run button during a run).
- Tooltips on hover (e.g., "Full run will create a new version; rerun from a stage will clone prior version and re-execute from that phase").
- Keyboard shortcuts (optional, future):
  - Ctrl+R or Cmd+R to launch Run.
  - Ctrl+S or Cmd+S to save in Prompts tab.

---

## Notes

- **Mobile-unfriendly**: This layout is desktop-only (Flet can target mobile, but the workspace tabs and multi-column layout assume a wider screen).
- **Dark mode support**: Can be added via Flet's theme engine if needed.
- **Real-time updates**: Event log updates in real-time as events arrive from RunController.
- **Version browsing**: RepositoryService handles version discovery; OutputPage just calls methods and displays results.

