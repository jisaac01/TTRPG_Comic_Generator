# Implementation plan: localhost web port of the Flet GUI

Parent strategy: [PLAN_web_and_user_flow.md](PLAN_web_and_user_flow.md). This plan is **Phase 1 only** — a faithful port of the current Flet app for single-user localhost use.

**Do not** add the guided first-run wizard, rewrite versioning, add auth/tenants, or deploy hosted. Those come later. Hosted multi-tenant is a stated destination; this port should leave cheap seams, not stub tenants.

**Process:** implement in local slices. Do not open GitHub PRs or merge — the owner reviews and merges.

**Bind:** `127.0.0.1:8765`. Do not use 8080 or 8050 (used by `portfolio_backtesting`).

---

## Goal

Replace the Flet presentation with a local web app that can do everything the current GUI does, talking only to the existing Python services:

- `RunController` + `RunConfig` + `PipelineEvent`
- `RepositoryService`
- `SettingsService`
- `ImageGenerator.generate_prompt_images` / stitch helpers
- `art_styles`, `model_catalog`, prompt template load/save

Pipeline tests stay the spec. `ComicPipeline` and versioning are frozen.

Success: you can run campaigns from a browser on `http://127.0.0.1:8765` as the daily driver, with Flet still launchable until a follow-up deletes it.

---

## Non-goals

- Guided stage wizard, per-stage direction UX, per-file versioning
- Auth, users, tenants, billing, cloud storage, job queues, Docker, reverse proxies
- Letting the client set `campaigns_root` or pass filesystem paths
- Rewriting Flet in place, or deleting Flet in the first slice
- npm / React / Vue / a design system
- Pixel-identical Flet layout

---

## Key decisions

### 1. JSON API is the product; HTML is disposable

FastAPI app wrapping existing services. The browser is a client of that API.

Later hosted work (and the wizard) should be able to replace the frontend without rewriting run/repository/settings logic. HTMX would couple HTML to the backend and get thrown away twice. A JSON API is the seam that survives.

### 2. No framework frontend

Server-served static HTML + a small amount of vanilla JS (fetch, EventSource for SSE, `<img>` preview, textarea editors). No build step.

Tabs: Run, Prompts, Output, plus Settings and a collapsible event log — same information architecture as Flet.

### 3. Bind localhost, inject `campaigns_root` on the server

- Default bind: `127.0.0.1:8765` (not `0.0.0.0`, not 8080/8050)
- `campaigns_root` comes from `app_paths.default_campaigns_root()` / env, same as today
- Request bodies **must not** include `campaigns_root` or absolute paths

That is the hosted seam: later a tenant resolver swaps `campaigns_root` (and settings) per request. Do not add `tenant_id` fields now.

### 4. Runs have an id even though only one can run

Keep `RunController`'s single-active-run rule. `POST /api/runs` still returns `{id, status}` and events stream at `/api/runs/{id}/events`. Hosted will replace the controller with a queue; the HTTP shape can stay.

### 5. Never leak raw disk paths to the client

Resources are identified as `campaign` / `episode` / `version` (`working` or `vNNN`) / relative file key (`03_script_page_001.json`, `images/…/05_page_1.png`).

Reject `..`, absolute paths, and writes outside the selected version dir. Required for localhost; load-bearing for hosted.

Drop Flet's "Open folder in Finder". Show the logical version label; optional copy of the server-side path in a settings/debug line is fine, but the UI must not depend on local filesystem access.

### 6. Extract GUI-only filesystem walks into `RepositoryService` (small, TDD)

`gui.py` currently lists version files, decides editability, and talks to `ImageGenerator` directly. The web layer should not copy that. Add service methods + tests, then call them from the API. Do not rewire Flet unless it is trivial; duplication for one release is acceptable.

### 7. Keep Flet until the web app is the daily driver

`python src/main.py` continues to work. New entry: `python src/web_main.py`. Delete Flet in a **follow-up** after real use — not as part of the first merge.

---

## Testing conventions (required)

- **Arrange / Act / Assert.** Prefer that shape. `Arrange Act Assert Act Assert` (and similar) is fine when it cuts duplication and reads better than two nearly identical tests.
- **Do not mock internal methods** when a real object plus a temp directory will do. Exceptions: determinism, tests that would otherwise be very slow, or setup/cleanup that is truly excessive.
- **Always mock external APIs** (LLM, Gemini image, Playwright scrape, keyring, live Gemini model list). Block real credentials at the highest level (`tests/conftest.py`) so future tests inherit the guard.
- **If an internal boundary is mocked** for simplicity, add integration tests that exercise several major paths through the real boundary.
- **Do not be lazy.** Assert on files written, JSON bodies, events, and HTTP status — not mock call counts alone.
- **Do not test negatives** (that a feature is absent), configuration trivia, or incidentals such as page color.

Web tests are HTTP-level (`TestClient` + temp `campaigns_root`). Inject `AppServices` with a fake `PipelineFactory` the same way `test_run_controller.py` does. Do not mock `RepositoryService` internals. Do not port Flet widget tests.

---

## Layout

```
src/web/
  app.py          # FastAPI factory: create_services(), bind config
  schemas.py      # request/response models (no Path fields for clients)
  api/
    campaigns.py
    prompts.py
    versions.py
    runs.py
    images.py
    settings.py
  static/         # HTML/CSS/JS
src/web_main.py   # uvicorn entry, 127.0.0.1:8765
tests/test_web_api.py
tests/test_web_runs.py
```

Keep this thin. Routers call services; they do not import `ComicPipeline` internals.

Dependencies: `fastapi`, `uvicorn`, `httpx` (TestClient).

---

## API surface (parity with Flet)

All JSON unless noted. Prefix `/api`.

### Catalog

| Method | Path | Flet equivalent |
|---|---|---|
| GET | `/api/health` | Playwright preflight + ready |
| GET | `/api/campaigns` | campaign dropdowns |
| POST | `/api/campaigns` | Add Campaign |
| GET | `/api/campaigns/{c}/episodes` | episode dropdowns + image-icon flags |
| GET | `/api/campaigns/{c}/art-styles` | art style dropdown (bundled + campaign) |

### Prompts workspace

| Method | Path | Flet equivalent |
|---|---|---|
| GET | `/api/campaigns/{c}/prompts` | file list + which exist |
| GET | `/api/campaigns/{c}/prompts/{key}` | load editor |
| PUT | `/api/campaigns/{c}/prompts/{key}` | save (campaign file; bundled style → campaign override) |
| POST | `/api/campaigns/{c}/prompts/{key}/reset` | Reset to Default |

### Output workspace

| Method | Path | Flet equivalent |
|---|---|---|
| GET | `/api/campaigns/{c}/episodes/{e}/versions` | version dropdown including `working` |
| PATCH | `/api/campaigns/{c}/episodes/{e}/versions/{v}` | star / note (`vNNN` only) |
| GET | `/api/campaigns/{c}/episodes/{e}/versions/{v}/status` | `run_status.json` summary |
| GET | `/api/campaigns/{c}/episodes/{e}/versions/{v}/files` | file list |
| GET | `/api/campaigns/{c}/episodes/{e}/versions/{v}/files/{key}` | text preview (pretty JSON) |
| PUT | `/api/campaigns/{c}/episodes/{e}/versions/{v}/files/{key}` | save — **working + non-image only** |
| GET | `/api/campaigns/{c}/episodes/{e}/versions/{v}/media/{key}` | image bytes |

### Runs

| Method | Path | Flet equivalent |
|---|---|---|
| POST | `/api/runs` | Run / Output Rerun |
| GET | `/api/runs/current` | busy state, phase |
| GET | `/api/runs/{id}/events` | SSE of `PipelineEvent.to_dict()` |
| POST | `/api/runs/{id}/cancel` | cancel |

`POST /api/runs` body is `RunConfig` fields **except** `campaigns_root` and prompt Path overrides. Server fills those. 409 if a run is already active.

### Images

| Method | Path | Flet equivalent |
|---|---|---|
| POST | `/api/campaigns/{c}/episodes/{e}/versions/{v}/images/generate` | Generate Images |
| POST | `.../images/generate-selected` | refresh icon on a prompt file |
| POST | `.../images/test` | Test Image |
| POST | `.../images/stitch` | Stitch |

### Settings

| Method | Path | Flet equivalent |
|---|---|---|
| GET | `/api/settings` | dialog contents (masked key + `configured: bool`) |
| PUT | `/api/settings` | save; `apply_to_environment()` |
| POST | `/api/settings/refresh-models` | Fetch Gemini models |

---

## Frontend parity checklist

Same three workspaces. Behavior, not pixels. Drop Finder open and macOS-specific image clipboard.

---

## Implementation slices (local; owner reviews and merges)

Each slice is tests → code → green. Keep Flet working throughout. Do not open GitHub PRs from this work.

### Slice 1 — App factory + health + campaigns

- FastAPI factory, `create_services(campaigns_root=…)`
- `GET /api/health`, `GET/POST /api/campaigns`
- `web_main.py` binds `127.0.0.1:8765`
- README: how to launch
- No three-tab UI yet; a one-line landing page is enough

### Slice 2 — Read APIs for Output + Prompts

### Slice 3 — Mutations (prompt save, working file save, star/note, settings)

### Slice 4 — Runs + SSE

### Slice 5 — Image jobs

### Slice 6 — Static UI parity

### Follow-up (not this plan)

- Daily-driver use, then delete Flet
- Then guided first-run (strategy Phase 3)

---

## Hosted multi-tenant: cheap seams only

Do now: resource URLs without machine paths; server-injected `campaigns_root` and settings; run ids; bind 127.0.0.1; path traversal rejection.

Do not now: users, queues, object storage, Playwright worker pools, `tenant_id` prefixes, replacing keyring "for later".

---

## If we implement next

Start Slice 1 only: failing tests for health + list/create campaign against a temp `campaigns_root`, then the FastAPI factory and `web_main.py`. Do not build the three-tab UI until read APIs exist.
