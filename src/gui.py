"""Flet GUI shell for TTRPG Comic Generator."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from app_paths import default_campaigns_root
from art_styles import (
    DEFAULT_ART_STYLE_STEM,
    campaign_art_direction_dir,
    default_art_direction_template_path,
    default_art_style,
    list_art_styles,
    parse_style_id,
    style_id,
)
from model_defaults import DEFAULT_MODEL
from pipeline_config import (
    STAGE_ORDER,
    AspectRatio,
    RecapVersion,
    RunConfig,
    required_rerun_for_config_diff,
    setting_field_enabled,
)
from pipeline_events import (
    PhaseError,
    PhasePartialFailure,
    PhaseStarted,
    RunCompleted,
    PipelineEventUnion,
)
from image_generator import ImageGenerator
from image_stitcher import stitch_panel_images
from prompt_templates import DEFAULT_PROMPTS_DIR
from repository_service import WORKING_DIR_NAME, CampaignPrompts, RepositoryService
from run_controller import RunController
from scraper import configure_playwright_runtime, normalize_recap_version, playwright_browser_executable
from settings_service import SettingsService

try:
    import flet as ft
except ImportError:  # pragma: no cover - handled by smoke test skip path
    ft = None


EVENT_LOG_LIMIT = 100
LOADING_GIF_URL = "https://upload.wikimedia.org/wikipedia/commons/b/b1/Loading_icon.gif"

_STAGE_LABELS: list[tuple[str, str]] = [
    ("scrape", "Scrape"),
    ("entities", "Entities"),
    ("architect", "Architect"),
    ("script", "Script"),
    ("style", "Style"),
    ("prompt", "Prompt"),
]


@dataclass(frozen=True)
class AppServices:
    repository: RepositoryService
    settings: SettingsService
    run_controller: RunController


def create_services(campaigns_root: Path | None = None) -> AppServices:
    resolved_campaigns_root = campaigns_root or default_campaigns_root()

    return AppServices(
        repository=RepositoryService(resolved_campaigns_root),
        settings=SettingsService(),
        run_controller=RunController(),
    )


def _playwright_preflight_warnings() -> list[str]:
    warnings: list[str] = []
    browser_root = configure_playwright_runtime()

    try:
        import playwright.async_api  # noqa: F401
    except Exception:
        return [
            "Playwright is not installed. Install dependencies before building the app."
        ]

    executable = playwright_browser_executable(browser_root)
    if executable is None or not executable.exists():
        return [
            "Playwright Chromium browser was not found in `src/playwright-browsers` or the standard Playwright browser cache. Install with `python -m playwright install chromium`, or rebuild with `PLAYWRIGHT_BROWSERS_PATH=src/playwright-browsers` in the build environment."
        ]

    try:
        subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except Exception as exc:
        detail = str(exc).lower()
        if "dll load failed" in detail or "vcruntime" in detail or "msvcp" in detail:
            warnings.append(
                "Playwright runtime dependency missing. On Windows install Microsoft Visual C++ Redistributable (x64)."
            )
        else:
            warnings.append(
                f"Playwright preflight check failed: {exc}"
            )

    return warnings


def build_run_page(
    services: AppServices,
    page: Any,
    event_log: Any,
    _ft: Any,
    on_campaign_created: Any | None = None,
    on_run_finished: Any | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Build the Run workspace controls.

    Returns a ``(container, state)`` tuple where *state* exposes individual
    controls plus callable hooks used by tests.
    """
    campaigns = services.repository.list_campaigns()

    campaign_dropdown = _ft.Dropdown(label="Campaign", options=[], width=220)
    new_campaign_field = _ft.TextField(label="New campaign", width=220)
    campaign_add_button = _ft.OutlinedButton("Add Campaign")
    campaign_status_text = _ft.Text("", size=12)

    run_mode_dropdown = _ft.Dropdown(
        label="Run Mode",
        value="story_url",
        options=[
            _ft.dropdown.Option("story_url", "Story URL"),
            _ft.dropdown.Option("existing_episode", "Existing Episode"),
        ],
        width=200,
    )
    url_field = _ft.TextField(label="Story URL", expand=True, hint_text="https://...")
    episode_dropdown = _ft.Dropdown(label="Episode", options=[], width=320, visible=False)
    rerun_dropdown = _ft.Dropdown(
        label="Start Stage",
        value="scrape",
        options=[_ft.dropdown.Option(stage, label) for stage, label in _STAGE_LABELS],
        width=170,
        disabled=True,
    )
    recap_dropdown = _ft.Dropdown(
        label="Recap",
        value="standard",
        options=[
            _ft.dropdown.Option("standard"),
            _ft.dropdown.Option("short"),
            _ft.dropdown.Option("alternate"),
            _ft.dropdown.Option("long"),
        ],
        width=140,
    )
    skip_style_checkbox = _ft.Checkbox(label="Skip style", value=False)
    generate_images_checkbox = _ft.Checkbox(label="Generate images", value=False)
    vignette_checkbox = _ft.Checkbox(label="Vignette (one scene)", value=False)
    panel_count_field = _ft.TextField(label="Panels", value="6", width=80)
    total_pages_field = _ft.TextField(label="Pages", value="1", width=80)
    generation_mode_dropdown = _ft.Dropdown(
        label="Generation mode",
        value="page",
        options=[
            _ft.dropdown.Option("page", "Page by Page"),
            _ft.dropdown.Option("panel", "Panel by Panel"),
        ],
        width=180,
    )
    aspect_ratio_dropdown = _ft.Dropdown(
        label="Aspect ratio",
        value="3:2",
        options=[
            _ft.dropdown.Option("1:1", "1:1 — Square"),
            _ft.dropdown.Option("4:3", "4:3 — Vertical / Portrait"),
            _ft.dropdown.Option("3:2", "3:2 — Standard comic page"),
        ],
        width=180,
    )
    art_style_dropdown = _ft.Dropdown(
        label="Art style",
        options=[],
        width=240,
    )
    run_button = _ft.Button("Run", disabled=False)
    running_ring = _ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
    running_gif = _ft.Image(src=LOADING_GIF_URL, width=20, height=20, visible=False)
    running_text = _ft.Text("Running...", size=12, visible=False)
    phase_badge = _ft.Text("", size=12, italic=True)
    status_summary = _ft.Text("", size=13, weight=_ft.FontWeight.W_600)
    run_error_text = _ft.Text("", size=12, color=_ft.Colors.RED_700, selectable=True)
    version_text = _ft.Text("", size=11, selectable=True)

    _episodes_by_slug: dict[str, Any] = {}

    def _list_episodes(campaign: str) -> list[Any]:
        lister = getattr(services.repository, "list_episodes", None)
        if callable(lister):
            episodes = lister(campaign)
            return episodes if isinstance(episodes, list) else []
        return []

    def _refresh_campaign_options(selected: str | None = None) -> None:
        current = selected if selected is not None else campaign_dropdown.value
        campaigns_now = services.repository.list_campaigns()
        campaign_dropdown.options = [_ft.dropdown.Option(c) for c in campaigns_now]
        if current and current in campaigns_now:
            campaign_dropdown.value = current
        else:
            campaign_dropdown.value = campaigns_now[0] if campaigns_now else None

    def _refresh_art_style_options(preferred: str | None = None) -> None:
        campaign = campaign_dropdown.value or ""
        styles = list_art_styles(services.repository.campaigns_root, campaign)
        art_style_dropdown.options = [
            _ft.dropdown.Option(s.id, s.label) for s in styles
        ]
        ids = {s.id for s in styles}
        if preferred and preferred in ids:
            art_style_dropdown.value = preferred
        elif art_style_dropdown.value in ids:
            pass
        elif styles:
            try:
                art_style_dropdown.value = default_art_style(
                    services.repository.campaigns_root, campaign
                ).id
            except FileNotFoundError:
                art_style_dropdown.value = styles[0].id
        else:
            art_style_dropdown.value = None

    def _refresh_episode_options() -> None:
        _episodes_by_slug.clear()
        episode_dropdown.options = []
        campaign = campaign_dropdown.value or ""
        episodes = _list_episodes(campaign)
        for ep in episodes:
            _episodes_by_slug[ep.slug] = ep
            episode_dropdown.options.append(_ft.dropdown.Option(ep.slug))
        if episodes:
            episode_dropdown.value = episodes[-1].slug
        else:
            episode_dropdown.value = None

    def _sync_mode_controls() -> None:
        mode = run_mode_dropdown.value or "story_url"
        is_story_url = mode == "story_url"
        url_field.visible = is_story_url
        episode_dropdown.visible = not is_story_url
        rerun_dropdown.disabled = is_story_url
        if is_story_url:
            rerun_dropdown.value = "scrape"
        elif rerun_dropdown.value == "scrape" or rerun_dropdown.value is None:
            rerun_dropdown.value = "architect"

    def _set_busy_state(busy: bool) -> None:
        run_button.disabled = busy
        running_ring.visible = busy
        running_gif.visible = busy
        running_text.visible = busy
        if busy:
            status_summary.value = ""
            run_error_text.value = ""

    def _build_config() -> RunConfig:
        mode = run_mode_dropdown.value or "story_url"
        selected_episode = _episodes_by_slug.get(episode_dropdown.value or "")
        if mode == "story_url":
            url = url_field.value or ""
            rerun = "scrape"
        else:
            url = selected_episode.url if selected_episode and selected_episode.url else (url_field.value or "")
            rerun = rerun_dropdown.value or "architect"
        return RunConfig(
            url=url,
            campaign=campaign_dropdown.value or "",
            rerun_from=rerun,
            recap_version=recap_dropdown.value or "standard",  # type: ignore[arg-type]
            skip_style=bool(skip_style_checkbox.value),
            generate_images=bool(generate_images_checkbox.value),
            image_generation_model=services.settings.get_image_generation_model(),
            panel_count=int(panel_count_field.value or 6),
            total_pages=int(total_pages_field.value or 1),
            aspect_ratio=aspect_ratio_dropdown.value or "3:2",
            generation_mode=generation_mode_dropdown.value or "page",
            vignette=bool(vignette_checkbox.value),
            art_style=art_style_dropdown.value or None,
        )

    def on_pipeline_event(event: PipelineEventUnion) -> None:
        if isinstance(event, PhaseStarted):
            phase_badge.value = f"Stage: {event.phase} - {event.message}"
        elif isinstance(event, PhaseError):
            run_error_text.value = f"{event.phase} failed: {event.error or event.message}"
        elif isinstance(event, PhasePartialFailure):
            detail = event.error_detail or event.message
            run_error_text.value = f"{event.phase} partial failure: {detail}"
        elif isinstance(event, RunCompleted):
            _set_busy_state(False)
            if event.status == "ok":
                status_summary.value = "✓ OK"
                run_error_text.value = ""
            elif event.status == "partial":
                status_summary.value = "⚠ Partial"
                if event.error_messages:
                    run_error_text.value = "\n".join(event.error_messages)
            else:
                status_summary.value = "✗ Failed"
                if event.error_messages:
                    run_error_text.value = "\n".join(event.error_messages)
            if event.version_dir:
                version_text.value = str(event.version_dir)
        append_pipeline_event(event_log, event, _ft)
        page.update()

    async def _execute_run() -> None:
        try:
            config = _build_config()
            task = services.run_controller.launch_run(config, on_pipeline_event)
            result = await task
            if result and result.status != "ok":
                details = result.error_details or result.errors
                if result.status == "partial":
                    status_summary.value = "⚠ Partial"
                elif result.status == "cancelled":
                    status_summary.value = "✗ Cancelled"
                else:
                    status_summary.value = "✗ Failed"
                if details:
                    run_error_text.value = "\n".join(details)
                page.update()
            if callable(on_run_finished):
                on_run_finished(result.version_dir if result else None)
        except (RuntimeError, ValueError) as exc:
            status_summary.value = f"✗ {exc}"
            run_error_text.value = str(exc)
            append_log_line(event_log, "Run", str(exc), _ft)
            _set_busy_state(False)
            page.update()
        finally:
            _set_busy_state(False)
            page.update()

    def on_run_click(_event: Any) -> None:
        _set_busy_state(True)
        page.update()
        page.run_task(_execute_run)

    def on_campaign_changed(event: Any) -> None:
        selected = _extract_change_value(event)
        if selected is not None:
            campaign_dropdown.value = selected
        _refresh_episode_options()
        _refresh_art_style_options()
        _sync_mode_controls()
        page.update()

    def on_mode_changed(_event: Any) -> None:
        _sync_mode_controls()
        page.update()

    def on_add_campaign(_event: Any) -> None:
        creator = getattr(services.repository, "create_campaign", None)
        name = (new_campaign_field.value or "").strip()
        if not callable(creator):
            campaign_status_text.value = "Campaign creation is unavailable"
            page.update()
            return
        try:
            creator(name)
        except FileExistsError:
            campaign_status_text.value = "Campaign already exists"
        except ValueError as exc:
            campaign_status_text.value = str(exc)
        except OSError as exc:
            campaign_status_text.value = f"Unable to create campaign: {exc}"
        else:
            campaign_status_text.value = "Campaign created"
            new_campaign_field.value = ""
            _refresh_campaign_options(selected=name)
            _refresh_episode_options()
            if callable(on_campaign_created):
                on_campaign_created(name)
        page.update()

    run_button.on_click = on_run_click
    _bind_dropdown_handler(campaign_dropdown, on_campaign_changed)
    _bind_dropdown_handler(run_mode_dropdown, on_mode_changed)
    campaign_add_button.on_click = on_add_campaign

    _refresh_campaign_options(campaigns[0] if campaigns else None)
    _refresh_episode_options()
    _refresh_art_style_options()
    _sync_mode_controls()

    container = _ft.Column(
        controls=[
            _ft.Text("Run", size=18, weight=_ft.FontWeight.W_600),
            _ft.Row([campaign_dropdown, new_campaign_field, campaign_add_button], spacing=12),
            campaign_status_text,
            _ft.Row([run_mode_dropdown, url_field, episode_dropdown], spacing=12),
            _ft.Row([rerun_dropdown, recap_dropdown, skip_style_checkbox, generate_images_checkbox], spacing=12),
            _ft.Row(
                [
                    panel_count_field,
                    total_pages_field,
                    generation_mode_dropdown,
                    vignette_checkbox,
                    aspect_ratio_dropdown,
                    art_style_dropdown,
                ],
                spacing=12,
            ),
            _ft.Row([run_button, running_ring, running_gif, running_text, phase_badge, status_summary], spacing=12),
            run_error_text,
            version_text,
        ],
        spacing=8,
    )

    return container, {
        "campaign_dropdown": campaign_dropdown,
        "new_campaign_field": new_campaign_field,
        "campaign_add_button": campaign_add_button,
        "campaign_status_text": campaign_status_text,
        "run_mode_dropdown": run_mode_dropdown,
        "url_field": url_field,
        "episode_dropdown": episode_dropdown,
        "rerun_dropdown": rerun_dropdown,
        "recap_dropdown": recap_dropdown,
        "skip_style_checkbox": skip_style_checkbox,
        "generate_images_checkbox": generate_images_checkbox,
        "vignette_checkbox": vignette_checkbox,
        "panel_count_field": panel_count_field,
        "total_pages_field": total_pages_field,
        "generation_mode_dropdown": generation_mode_dropdown,
        "aspect_ratio_dropdown": aspect_ratio_dropdown,
        "art_style_dropdown": art_style_dropdown,
        "refresh_art_styles": _refresh_art_style_options,
        "run_button": run_button,
        "running_ring": running_ring,
        "running_gif": running_gif,
        "running_text": running_text,
        "phase_badge": phase_badge,
        "status_summary": status_summary,
        "run_error_text": run_error_text,
        "version_text": version_text,
        "on_pipeline_event": on_pipeline_event,
        "build_config": _build_config,
        "execute_run": _execute_run,
        "refresh_campaigns": _refresh_campaign_options,
    }


def _validate_art_template(text: str) -> str | None:
    """Return an error message if *text* is not a valid art direction template, else None."""
    from prompter import _normalize_art_template_object

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    if not isinstance(obj, dict):
        return "Template must be a JSON object"
    try:
        _normalize_art_template_object(obj, source="editor")
    except ValueError as exc:
        return str(exc)
    return None


_PROMPT_FILE_LABELS: list[tuple[str, str]] = [
    ("art_direction_template", f"art_direction/{DEFAULT_ART_STYLE_STEM}.json"),
    ("story_architect_system", "story_architect_system.txt"),
    ("story_architect_user", "story_architect_user.txt"),
    ("scriptwriter_system", "scriptwriter_system.txt"),
    ("scriptwriter_user", "scriptwriter_user.txt"),
    ("style_integrator_system", "style_integrator_system.txt"),
    ("style_integrator_user", "style_integrator_user.txt"),
    ("page_prompt", "page_prompt.txt"),
]


def _open_in_file_manager(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
        return
    if sys.platform == "win32":
        subprocess.run(["explorer", str(path)], check=False)
        return
    subprocess.run(["xdg-open", str(path)], check=False)


def build_prompt_page(
    services: AppServices, page: Any, _ft: Any
) -> tuple[Any, dict[str, Any]]:
    """Build the Prompts workspace.

    Returns a ``(container, state)`` tuple where *state* exposes controls and
    hooks for tests.
    """
    campaign_dropdown = _ft.Dropdown(
        label="Campaign",
        options=[],
        value=None,
        width=220,
    )
    loading_ring = _ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    loading_text = _ft.Text("Reloading...", size=12, visible=False)

    file_list = _ft.RadioGroup(
        content=_ft.Column(
            spacing=2,
            scroll=_ft.ScrollMode.AUTO,
        )
    )

    editor = _ft.TextField(
        multiline=True,
        min_lines=20,
        max_lines=40,
        expand=True,
        text_style=_ft.TextStyle(font_family="monospace", size=12),
    )
    copy_content_button = _ft.IconButton(
        icon=_ft.Icons.CONTENT_COPY,
        tooltip="Copy to clipboard",
    )

    validation_text = _ft.Text("", color=_ft.Colors.RED_700, size=12)
    capture_preview_text = _ft.Text(
        "Art styles: bundled library + campaign overrides. "
        "Saving a bundled style writes a campaign override; pick \"… (campaign)\" on Run/Output to use it.",
        size=12,
    )
    source_dir_text = _ft.Text("Source directory: -", size=12, selectable=True)

    # Tracks which field key is selected (e.g. "bundled:brutalist" or "scriptwriter_system")
    _selected_key: list[str] = [""]
    # Maps field key -> path for load/save
    _paths: dict[str, Path] = {}
    # Tracks the directory currently used for loading selected prompt
    _active_source_dir: list[Path | None] = [None]
    # Optional callback after art style save (e.g. refresh Run/Output dropdowns)
    _on_art_styles_changed: list[Any] = [None]

    def _set_loading(loading: bool) -> None:
        loading_ring.visible = loading
        loading_text.visible = loading

    def _refresh_campaign_options(selected: str | None = None) -> None:
        current = selected if selected is not None else campaign_dropdown.value
        campaigns = services.repository.list_campaigns()
        campaign_dropdown.options = [_ft.dropdown.Option(c) for c in campaigns]
        if current and current in campaigns:
            campaign_dropdown.value = current
        else:
            campaign_dropdown.value = campaigns[0] if campaigns else None

    def _is_art_style_key(key: str) -> bool:
        return key.startswith("bundled:") or key.startswith("campaign:")

    def _default_prompt_path_for_key(key: str) -> Path | None:
        if _is_art_style_key(key):
            try:
                source, stem = parse_style_id(key)
            except ValueError:
                return default_art_direction_template_path()
            if source == "bundled":
                return default_art_direction_template_path().parent / f"{stem}.json"
            campaign = campaign_dropdown.value or ""
            return campaign_art_direction_dir(
                services.repository.campaigns_root, campaign
            ) / f"{stem}.json"
        filename = next((fn for k, fn in _PROMPT_FILE_LABELS if k == key), None)
        if not filename:
            return None
        return DEFAULT_PROMPTS_DIR / filename

    def _get_prompts() -> CampaignPrompts | None:
        campaign = campaign_dropdown.value
        if not campaign:
            return None
        return services.repository.get_campaign_prompts(campaign)

    def _resolve_source_path_for_key(key: str) -> Path | None:
        mapped = _paths.get(key)
        if mapped and mapped.exists():
            return mapped
        if _is_art_style_key(key):
            # Bundled always loads from library; campaign may fall back to matching bundled.
            try:
                source, stem = parse_style_id(key)
            except ValueError:
                return default_art_direction_template_path()
            if source == "bundled":
                return default_art_direction_template_path().parent / f"{stem}.json"
            if mapped is not None and mapped.exists():
                return mapped
            # Campaign file missing: show bundled content as a starting point.
            bundled = default_art_direction_template_path().parent / f"{stem}.json"
            return bundled if bundled.exists() else mapped
        return _default_prompt_path_for_key(key)

    def _refresh_file_list() -> None:
        rows = file_list.content
        rows.controls.clear()
        _paths.clear()
        prompts = _get_prompts()
        campaign = campaign_dropdown.value or ""

        # Full art style list: all bundled + this campaign's overrides.
        styles = list_art_styles(services.repository.campaigns_root, campaign)
        for option in styles:
            _paths[option.id] = option.path
            exists_mark = "" if option.path.exists() else " ✗"
            rows.controls.append(
                _ft.Radio(value=option.id, label=f"{option.label}{exists_mark}")
            )

        if prompts is not None:
            for key, filename in _PROMPT_FILE_LABELS:
                if key == "art_direction_template":
                    continue
                path = getattr(prompts, key)
                _paths[key] = path
                source_path = path if path.exists() else _default_prompt_path_for_key(key)
                exists_mark = "" if (source_path and source_path.exists()) else " ✗"
                rows.controls.append(
                    _ft.Radio(value=key, label=f"{filename}{exists_mark}")
                )
        else:
            for key, filename in _PROMPT_FILE_LABELS:
                if key == "art_direction_template":
                    continue
                source_path = _default_prompt_path_for_key(key)
                exists_mark = "" if (source_path and source_path.exists()) else " ✗"
                rows.controls.append(
                    _ft.Radio(value=key, label=f"{filename}{exists_mark}")
                )

    def _select_default_file() -> None:
        options = file_list.content.controls
        if not options:
            file_list.value = None
            _selected_key[0] = ""
            editor.value = ""
            return
        first_value = options[0].value
        file_list.value = first_value
        _selected_key[0] = first_value
        _load_selected()

    def _load_selected() -> None:
        key = _selected_key[0]
        source_path = _resolve_source_path_for_key(key)
        validation_text.value = ""
        editor.border_color = None
        if not source_path or not source_path.exists():
            editor.value = ""
            _active_source_dir[0] = None
            source_dir_text.value = "Source directory: -"
            return
        editor.value = source_path.read_text(encoding="utf-8")
        _active_source_dir[0] = source_path.parent
        source_dir_text.value = f"Source directory: {source_path.parent}"

    def _on_file_selected(e: Any) -> None:
        selected = _extract_change_value(e)
        _selected_key[0] = selected or ""
        _load_selected()
        page.update()

    file_list.on_change = _on_file_selected

    def on_load(_e: Any) -> None:
        _set_loading(True)
        page.update()
        _load_selected()
        _set_loading(False)
        page.update()

    def on_save(_e: Any) -> None:
        key = _selected_key[0]
        text = editor.value or ""
        if _is_art_style_key(key):
            err = _validate_art_template(text)
            if err:
                validation_text.value = err
                editor.border_color = _ft.Colors.RED_700
                page.update()
                return
            campaign = campaign_dropdown.value or ""
            if not campaign:
                validation_text.value = "Select a campaign before saving an art style."
                editor.border_color = _ft.Colors.RED_700
                page.update()
                return
            try:
                source, stem = parse_style_id(key)
            except ValueError as exc:
                validation_text.value = str(exc)
                editor.border_color = _ft.Colors.RED_700
                page.update()
                return
            # Never mutate the bundled library from the GUI: always write a campaign override.
            path = campaign_art_direction_dir(
                services.repository.campaigns_root, campaign
            ) / f"{stem}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            saved_id = style_id("campaign", stem)
            editor.border_color = None
            _refresh_file_list()
            _selected_key[0] = saved_id
            file_list.value = saved_id
            _load_selected()
            # Set after load: _load_selected clears validation_text.
            validation_text.value = (
                f"Saved campaign override → art_direction/{stem}.json. "
                f"On Run/Output select \"{stem} (campaign)\" to use it."
            )
            callback = _on_art_styles_changed[0]
            if callable(callback):
                callback()
            page.update()
            return

        path = _paths.get(key)
        if not path:
            return
        validation_text.value = ""
        editor.border_color = None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _refresh_file_list()
        _load_selected()
        page.update()

    def on_reset(_e: Any) -> None:
        key = _selected_key[0]
        if not key:
            return
        if _is_art_style_key(key):
            try:
                _source, stem = parse_style_id(key)
            except ValueError:
                return
            default_path = default_art_direction_template_path().parent / f"{stem}.json"
            if not default_path.exists():
                default_path = default_art_direction_template_path()
        else:
            filename = next((fn for k, fn in _PROMPT_FILE_LABELS if k == key), None)
            if not filename:
                return
            default_path = DEFAULT_PROMPTS_DIR / filename
        if not default_path.exists():
            return
        editor.value = default_path.read_text(encoding="utf-8")
        _active_source_dir[0] = default_path.parent
        source_dir_text.value = f"Source directory: {default_path.parent}"
        validation_text.value = ""
        editor.border_color = None
        page.update()

    def on_open_prompts_folder(_e: Any) -> None:
        source_dir = _active_source_dir[0]
        if not source_dir or not source_dir.exists():
            source_dir_text.value = "Source directory: unavailable"
            page.update()
            return
        try:
            _open_in_file_manager(source_dir)
        except OSError:
            source_dir_text.value = f"Source directory: {source_dir} (open failed)"
            page.update()

    async def on_copy_source_path(_e: Any) -> None:
        key = _selected_key[0]
        source_path = _resolve_source_path_for_key(key)
        if source_path and source_path.exists():
            await _set_clipboard(page, str(source_path), _ft)
        page.update()

    async def on_copy_content(_e: Any) -> None:
        await _set_clipboard(page, editor.value or "", _ft)
        page.update()

    copy_content_button.on_click = on_copy_content

    def on_campaign_changed(event: Any) -> None:
        selected = _extract_change_value(event)
        if selected is not None:
            campaign_dropdown.value = selected
        _set_loading(True)
        page.update()
        _selected_key[0] = ""
        editor.value = ""
        _active_source_dir[0] = None
        source_dir_text.value = "Source directory: -"
        validation_text.value = ""
        editor.border_color = None
        _refresh_file_list()
        _select_default_file()
        _set_loading(False)
        page.update()

    _bind_dropdown_handler(campaign_dropdown, on_campaign_changed)

    _refresh_campaign_options()
    _refresh_file_list()
    _select_default_file()

    container = _ft.Column(
        controls=[
            _ft.Text("Prompts", size=18, weight=_ft.FontWeight.W_600),
            _ft.Row([campaign_dropdown, loading_ring, loading_text], spacing=8),
            capture_preview_text,
            _ft.Row(
                controls=[
                    source_dir_text,
                    _ft.OutlinedButton("Open Prompts Folder", on_click=on_open_prompts_folder),
                ],
                spacing=8,
            ),
            _ft.Row(
                controls=[
                    _ft.Container(
                        content=_ft.Column(
                            controls=[
                                _ft.Text("Files", size=13, weight=_ft.FontWeight.W_500),
                                _ft.Container(
                                    content=file_list,
                                    height=320,
                                    expand=True,
                                    clip_behavior=_ft.ClipBehavior.HARD_EDGE,
                                ),
                            ],
                            spacing=4,
                        ),
                        width=240,
                    ),
                    _ft.Column(
                        controls=[
                            _ft.Row(
                                controls=[copy_content_button],
                                alignment=_ft.MainAxisAlignment.END,
                            ),
                            editor,
                            validation_text,
                            _ft.Row(
                                controls=[
                                    _ft.FilledButton("Save", on_click=on_save),
                                    _ft.OutlinedButton("Load", on_click=on_load),
                                    _ft.OutlinedButton("Reset to Default", on_click=on_reset),
                                ],
                                spacing=8,
                            ),
                        ],
                        expand=True,
                        spacing=4,
                    ),
                ],
                expand=True,
                spacing=12,
                vertical_alignment=_ft.CrossAxisAlignment.START,
            ),
        ],
        expand=True,
        spacing=8,
    )

    def set_on_art_styles_changed(callback: Any) -> None:
        _on_art_styles_changed[0] = callback

    return container, {
        "campaign_dropdown": campaign_dropdown,
        "file_list": file_list,
        "editor": editor,
        "copy_content_button": copy_content_button,
        "validation_text": validation_text,
        "capture_preview_text": capture_preview_text,
        "source_dir_text": source_dir_text,
        "on_save": on_save,
        "on_load": on_load,
        "on_reset": on_reset,
        "on_copy_content": on_copy_content,
        "on_open_prompts_folder": on_open_prompts_folder,
        "refresh_file_list": _refresh_file_list,
        "refresh_campaigns": _refresh_campaign_options,
        "set_on_art_styles_changed": set_on_art_styles_changed,
        "selected_key": _selected_key,
        "paths": _paths,
    }


def _format_preview(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Unable to read file: {exc}"
    if path.suffix.lower() != ".json":
        return text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(parsed, indent=2, ensure_ascii=False)


async def _set_clipboard(page: Any, text: str, _ft: Any) -> None:
    """Copy *text* to the system clipboard.

    Prefer an explicit ``page.set_clipboard`` when present (tests / older Flet).
    Otherwise use Flet's ``Clipboard`` service (``await Clipboard().set(...)``),
    which is the supported API in current Flet releases.
    """
    setter = getattr(page, "set_clipboard", None)
    if callable(setter):
        result = setter(text)
        if inspect.isawaitable(result):
            await result
        return

    clipboard_cls = getattr(_ft, "Clipboard", None)
    if clipboard_cls is None:
        raise RuntimeError("Clipboard is unavailable: no Flet Clipboard service")
    await clipboard_cls().set(text)


def _extract_change_value(event: Any) -> str | None:
    data = getattr(event, "data", None)
    if isinstance(data, str):
        cleaned = data.strip()
        if cleaned and cleaned.lower() not in {"none", "null"}:
            return cleaned
    control_value = getattr(getattr(event, "control", None), "value", None)
    if isinstance(control_value, str):
        cleaned = control_value.strip()
        if cleaned:
            return cleaned
    return None


def _bind_dropdown_handler(dropdown: Any, handler: Any) -> None:
    # Flet 0.85 uses `on_select` for selection changes.
    if hasattr(dropdown, "on_select"):
        dropdown.on_select = handler
    # Keep a fallback for older API behavior.
    if hasattr(dropdown, "on_change"):
        dropdown.on_change = handler


def build_output_page(
    services: AppServices, page: Any, _ft: Any, event_log: Any | None = None
) -> tuple[Any, dict[str, Any]]:
    campaign_dropdown = _ft.Dropdown(
        label="Campaign",
        options=[],
        value=None,
        width=220,
    )
    episode_dropdown = _ft.Dropdown(label="Episode", options=[], width=320)
    version_dropdown = _ft.Dropdown(label="Version", options=[], width=140)
    loading_ring = _ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    loading_text = _ft.Text("Reloading...", size=12, visible=False)

    quick_rerun_stage_dropdown = _ft.Dropdown(
        label="Rerun from stage",
        value="architect",
        options=[_ft.dropdown.Option(stage, label) for stage, label in _STAGE_LABELS],
        width=190,
    )
    rerun_only_stage_checkbox = _ft.Checkbox(
        label="Rerun only this stage",
        value=False,
        tooltip="When on, re-runs the selected stage and stops; does not continue to later stages.",
    )
    quick_rerun_button = _ft.OutlinedButton("Rerun")
    quick_rerun_gif = _ft.Image(src=LOADING_GIF_URL, width=20, height=20, visible=False)
    quick_rerun_text = _ft.Text("Running...", size=12, visible=False)
    generate_images_button = _ft.OutlinedButton("Generate Images")
    stitch_images_button = _ft.OutlinedButton("Stitch")
    generate_selected_image_button = _ft.IconButton(
        icon=_ft.Icons.REFRESH,
        tooltip="Generate selected prompt image",
        visible=False,
    )

    file_list = _ft.RadioGroup(
        content=_ft.Column(
            spacing=2,
            scroll=_ft.ScrollMode.AUTO,
        )
    )
    preview = _ft.TextField(
        multiline=True,
        min_lines=20,
        max_lines=40,
        read_only=True,
        expand=True,
        text_style=_ft.TextStyle(font_family="monospace", size=12),
    )
    copy_content_button = _ft.IconButton(
        icon=_ft.Icons.CONTENT_COPY,
        tooltip="Copy to clipboard",
    )

    run_status_text = _ft.Text("", size=12, selectable=True)
    settings_text = _ft.Text("", size=12, selectable=True)
    panel_count_field = _ft.TextField(label="Panels", value="6", width=80)
    total_pages_field = _ft.TextField(label="Pages", value="1", width=80)
    recap_dropdown = _ft.Dropdown(
        label="Recap",
        value="standard",
        options=[
            _ft.dropdown.Option("standard"),
            _ft.dropdown.Option("short"),
            _ft.dropdown.Option("alternate"),
            _ft.dropdown.Option("long"),
        ],
        width=160,
    )
    aspect_ratio_settings_dropdown = _ft.Dropdown(
        label="Aspect ratio",
        value="3:2",
        options=[
            _ft.dropdown.Option("1:1", "1:1 — Square"),
            _ft.dropdown.Option("4:3", "4:3 — Vertical / Portrait"),
            _ft.dropdown.Option("3:2", "3:2 — Standard comic page"),
        ],
        width=200,
    )
    generation_mode_dropdown = _ft.Dropdown(
        label="Generation mode",
        value="page",
        options=[
            _ft.dropdown.Option("page", "Page by Page"),
            _ft.dropdown.Option("panel", "Panel by Panel"),
        ],
        width=180,
    )
    vignette_checkbox = _ft.Checkbox(label="Vignette (one scene)", value=False)
    art_style_dropdown = _ft.Dropdown(
        label="Art style",
        options=[],
        width=240,
    )
    version_path_text = _ft.Text("", size=11, selectable=True)
    output_status_text = _ft.Text("", size=12)

    _DEFAULT_RUN_CONFIG: dict[str, Any] = {
        "panel_count": 6,
        "total_pages": 1,
        "recap_version": "standard",
        "aspect_ratio": "3:2",
        "generation_mode": "page",
        "vignette": False,
        "art_style": None,
    }
    _committed_settings: dict[str, Any] = {}

    _episodes_by_slug: dict[str, Any] = {}
    _selected_version_dir: list[Path | None] = [None]
    _selected_files: dict[str, Path] = {}

    def _set_loading(loading: bool) -> None:
        loading_ring.visible = loading
        loading_text.visible = loading

    def _refresh_campaign_options(selected: str | None = None) -> None:
        current = selected if selected is not None else campaign_dropdown.value
        campaigns = services.repository.list_campaigns()
        campaign_dropdown.options = [_ft.dropdown.Option(c) for c in campaigns]
        if current and current in campaigns:
            campaign_dropdown.value = current
        else:
            campaign_dropdown.value = campaigns[0] if campaigns else None

    def _refresh_episodes() -> None:
        campaign = campaign_dropdown.value or ""
        episodes = services.repository.list_episodes(campaign)
        _episodes_by_slug.clear()
        episode_dropdown.options = []
        for ep in episodes:
            _episodes_by_slug[ep.slug] = ep
            episode_dropdown.options.append(_ft.dropdown.Option(ep.slug))
        episode_dropdown.value = episodes[-1].slug if episodes else None

    def _refresh_versions() -> None:
        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        versions = services.repository.list_versions(campaign, episode_slug)
        options: list[Any] = []
        if services.repository.has_working(campaign, episode_slug):
            options.append(
                _ft.dropdown.Option(WORKING_DIR_NAME, f"{WORKING_DIR_NAME} (editable)")
            )
        options.extend(_ft.dropdown.Option(v.version) for v in versions)
        version_dropdown.options = options
        # Default to the latest historical version (what the last run produced).
        version_dropdown.value = versions[-1].version if versions else (
            WORKING_DIR_NAME if options else None
        )

    def _legacy_episode_run_config(campaign: str, episode_slug: str) -> dict[str, Any]:
        episode_dir = services.repository.campaigns_root / campaign / episode_slug
        meta_path = episode_dir / "episode_meta.json"
        if not meta_path.exists():
            return {}
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        legacy: dict[str, Any] = {}
        for key in _DEFAULT_RUN_CONFIG:
            if key in meta:
                legacy[key] = meta[key]
        return legacy

    def _load_version_run_config(campaign: str, episode_slug: str, version: str) -> dict[str, Any]:
        config = dict(_DEFAULT_RUN_CONFIG)
        if campaign and episode_slug and version:
            status = services.repository.run_status(campaign, episode_slug, version) or {}
            run_config = status.get("run_config")
            if isinstance(run_config, dict):
                config.update(run_config)
            else:
                config.update(_legacy_episode_run_config(campaign, episode_slug))
        return config

    def _refresh_art_style_options(preferred: str | None = None) -> None:
        campaign = campaign_dropdown.value or ""
        styles = list_art_styles(services.repository.campaigns_root, campaign)
        art_style_dropdown.options = [
            _ft.dropdown.Option(s.id, s.label) for s in styles
        ]
        ids = {s.id for s in styles}
        if preferred and preferred in ids:
            art_style_dropdown.value = preferred
        elif art_style_dropdown.value in ids:
            pass
        elif styles:
            try:
                art_style_dropdown.value = default_art_style(
                    services.repository.campaigns_root, campaign
                ).id
            except FileNotFoundError:
                art_style_dropdown.value = styles[0].id
        else:
            art_style_dropdown.value = None

    def _apply_settings_to_controls(config: dict[str, Any]) -> None:
        panel_count_field.value = str(config.get("panel_count", 6))
        total_pages_field.value = str(config.get("total_pages", 1))
        recap_dropdown.value = str(config.get("recap_version", "standard"))
        aspect_ratio_settings_dropdown.value = str(config.get("aspect_ratio", "3:2"))
        generation_mode_dropdown.value = str(config.get("generation_mode", "page"))
        vignette_checkbox.value = bool(config.get("vignette", False))
        preferred_style = config.get("art_style")
        _refresh_art_style_options(
            preferred=str(preferred_style) if preferred_style else None
        )

    def _read_settings_from_controls() -> dict[str, Any]:
        aspect_ratio = aspect_ratio_settings_dropdown.value or "3:2"
        if aspect_ratio not in {"1:1", "4:3", "3:2"}:
            aspect_ratio = "3:2"
        generation_mode = generation_mode_dropdown.value or "page"
        if generation_mode not in {"page", "panel"}:
            generation_mode = "page"
        return {
            "panel_count": int(panel_count_field.value or 6),
            "total_pages": int(total_pages_field.value or 1),
            "recap_version": normalize_recap_version(recap_dropdown.value or "standard"),
            "aspect_ratio": aspect_ratio,
            "generation_mode": generation_mode,
            "vignette": bool(vignette_checkbox.value),
            "art_style": art_style_dropdown.value or None,
        }

    def _apply_stage_gating() -> None:
        stage = quick_rerun_stage_dropdown.value or "architect"
        recap_dropdown.disabled = not setting_field_enabled("recap", stage)
        panel_count_field.disabled = not setting_field_enabled("panels", stage)
        total_pages_field.disabled = not setting_field_enabled("pages", stage)
        vignette_checkbox.disabled = not setting_field_enabled("vignette", stage)
        generation_mode_dropdown.disabled = not setting_field_enabled("generation_mode", stage)
        art_style_dropdown.disabled = not setting_field_enabled("art_style", stage)
        aspect_ratio_settings_dropdown.disabled = not setting_field_enabled("aspect_ratio", stage)

    def _sync_settings_controls() -> None:
        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        version = version_dropdown.value or ""
        config = _load_version_run_config(campaign, episode_slug, version)
        _committed_settings.clear()
        _committed_settings.update(config)
        _apply_settings_to_controls(config)
        _apply_stage_gating()

    def _set_episode_settings_text() -> None:
        config = _read_settings_from_controls()
        mode_label = (
            "Panel by Panel" if config.get("generation_mode") == "panel" else "Page by Page"
        )
        vignette_label = "on" if config.get("vignette") else "off"
        style_label = config.get("art_style") or "default"
        settings_text.value = (
            f"Panels: {config['panel_count']}  |  Pages: {config['total_pages']}  |  "
            f"Recap: {config['recap_version']}  |  Aspect ratio: {config['aspect_ratio']}  |  "
            f"Generation: {mode_label}  |  Vignette: {vignette_label}  |  Art style: {style_label}"
        )

    def _validate_rerun_settings() -> str | None:
        stage = quick_rerun_stage_dropdown.value or "architect"
        if stage not in STAGE_ORDER:
            return None
        required = required_rerun_for_config_diff(
            _committed_settings,
            _read_settings_from_controls(),
        )
        if required is None:
            return None
        if STAGE_ORDER.index(required) < STAGE_ORDER.index(stage):
            return (
                f"These settings require rerunning from {required} or earlier "
                f"(selected stage: {stage})."
            )
        return None

    def _build_rerun_config(
        campaign: str,
        episode_slug: str,
        stage: str,
        *,
        generate_images: bool = False,
    ) -> RunConfig:
        settings = _read_settings_from_controls()
        episode = _episodes_by_slug.get(episode_slug)
        url = episode.url if episode and episode.url else ""
        stop_after = cast(Any, stage) if bool(rerun_only_stage_checkbox.value) else None
        return RunConfig(
            url=url,
            campaign=campaign,
            rerun_from=cast(Any, stage),
            stop_after=stop_after,
            recap_version=cast(RecapVersion, settings["recap_version"]),
            skip_style=False,
            generate_images=generate_images,
            image_generation_model=services.settings.get_image_generation_model(),
            panel_count=int(settings["panel_count"]),
            total_pages=int(settings["total_pages"]),
            aspect_ratio=cast(AspectRatio, settings["aspect_ratio"]),
            generation_mode=cast(Any, settings["generation_mode"]),
            vignette=bool(settings.get("vignette", False)),
            art_style=settings.get("art_style"),
        )

    def _set_run_status() -> str | None:
        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        version = version_dropdown.value or ""
        if not (campaign and episode_slug and version):
            run_status_text.value = ""
            return None
        status = services.repository.run_status(campaign, episode_slug, version) or {}
        if not status:
            run_status_text.value = ""
            return None
        status_value = status.get("status", "unknown")
        checkpoints = ", ".join(status.get("checkpoints", []))
        failed = ", ".join(status.get("failed", []))
        errors = "; ".join(status.get("errors", []))
        warnings = "; ".join(status.get("warnings", []))
        parts = [f"status={status_value}"]
        if checkpoints:
            parts.append(f"checkpoints=[{checkpoints}]")
        if failed:
            parts.append(f"failed=[{failed}]")
        if errors:
            parts.append(f"errors=[{errors}]")
        if warnings:
            parts.append(f"warnings=[{warnings}]")
        run_status_text.value = " | ".join(parts)
        return str(status_value)

    def _list_version_files(version_dir: Path) -> list[Path]:
        preferred = [
            "01_raw_text.json",
            "02_entities.json",
            "02_5_story_bible.txt",
            "03_script.json",
            "03_5_styled_script.json",
            "04_page_1_prompt.txt",
            "run_status.json",
            "art_direction_template.json",
        ]
        files: list[Path] = []
        for name in preferred:
            path = version_dir / name
            if path.exists() and path.is_file():
                files.append(path)
        extra = sorted(
            p
            for p in version_dir.iterdir()
            if p.is_file() and p not in files
        )
        files.extend(extra)
        return files

    def _refresh_file_list(status_value: str | None = None) -> None:
        _selected_files.clear()
        rows = file_list.content
        rows.controls.clear()

        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        version = version_dropdown.value or ""
        if not (campaign and episode_slug and version):
            _selected_version_dir[0] = None
            version_path_text.value = ""
            return

        version_files = services.repository.get_version_files(campaign, episode_slug, version)
        version_dir = version_files.version_dir
        _selected_version_dir[0] = version_dir
        version_path_text.value = str(version_dir)

        if not version_dir.exists():
            return

        for path in _list_version_files(version_dir):
            key = path.name
            _selected_files[key] = path
            rows.controls.append(_ft.Radio(value=key, label=key))

        config = _load_version_run_config(campaign, episode_slug, version)
        if config.get("generation_mode") == "panel":
            panel_prompts = sorted(
                name
                for name in _selected_files
                if name.startswith("04_page_") and "_panel_" in name and name.endswith("_prompt.txt")
            )
            preferred_default = (
                "run_status.json"
                if status_value == "failed"
                else (panel_prompts[0] if panel_prompts else "04_page_1_prompt.txt")
            )
        else:
            preferred_default = "run_status.json" if status_value == "failed" else "04_page_1_prompt.txt"
        if preferred_default in _selected_files:
            file_list.value = preferred_default
        elif rows.controls:
            file_list.value = rows.controls[0].value
        else:
            file_list.value = None

    def _load_selected_file() -> None:
        selected = file_list.value
        if not selected:
            preview.value = ""
            generate_selected_image_button.visible = False
            return
        path = _selected_files.get(selected)
        if not path or not path.exists():
            preview.value = ""
            generate_selected_image_button.visible = False
            return
        try:
            preview.value = _format_preview(path)
        except Exception as exc:
            preview.value = f"Unable to render preview: {exc}"
        generate_selected_image_button.visible = path.name.startswith("04_page_") and path.name.endswith("_prompt.txt")

    def _selected_prompt_path() -> Path | None:
        selected = file_list.value
        if not selected:
            return None
        path = _selected_files.get(selected)
        return path if path and path.name.startswith("04_page_") and path.name.endswith("_prompt.txt") else None

    def _stitch_panel_images_for_page(page_number: int, version_dir: Path) -> Path:
        panel_paths = sorted(version_dir.glob(f"05_page_{page_number}_panel_*.png"))
        if not panel_paths:
            raise ValueError("No panel images available to stitch")

        output_path = version_dir / f"06_page_{page_number}.png"
        settings = _read_settings_from_controls()
        return stitch_panel_images(
            panel_paths,
            output_path,
            aspect_ratio=str(settings["aspect_ratio"]),
        )

    def _generate_prompt_image(prompt_path: Path) -> Path:
        version_dir = _selected_version_dir[0]
        if version_dir is None:
            raise ValueError("Select a version before generating images")

        match = re.search(r"04_page_(\d+)(?:_panel_(\d+))?_prompt\.txt$", prompt_path.name)
        if match is None:
            raise ValueError("Prompt file name is not a page prompt")

        page_number = match.group(1)
        panel_number = match.group(2)
        output_path = (
            version_dir / f"05_page_{page_number}_panel_{panel_number}.png"
            if panel_number is not None
            else version_dir / f"05_page_{page_number}.png"
        )
        generator = ImageGenerator(model=services.settings.get_image_generation_model())
        generator.save_image(generator.generate_image(prompt_path.read_text(encoding="utf-8")), output_path)

        if panel_number is not None:
            stitched_path = _stitch_panel_images_for_page(int(page_number), version_dir)
            return stitched_path if stitched_path.exists() else output_path
        return output_path

    async def _run_generate_selected_image() -> None:
        prompt_path = _selected_prompt_path()
        if prompt_path is None:
            _set_output_busy_state(False)
            output_status_text.value = "Select a page prompt to regenerate"
            page.update()
            return

        _set_output_busy_state(True)
        output_status_text.value = "Generating image..."
        page.update()
        try:
            output_path = await asyncio.to_thread(_generate_prompt_image, prompt_path)
            output_status_text.value = f"Generated {output_path.name}"
        except Exception as exc:
            output_status_text.value = f"Image generation failed: {exc}"
        finally:
            _set_output_busy_state(False)
            page.update()

    async def _run_stitch_images() -> None:
        version_dir = _selected_version_dir[0]
        if version_dir is None or not version_dir.exists():
            _set_output_busy_state(False)
            output_status_text.value = "Select a version before stitching images"
            page.update()
            return

        _set_output_busy_state(True)
        output_status_text.value = "Stitching panel images..."
        page.update()
        try:
            panel_paths = sorted(version_dir.glob("05_page_*_panel_*.png"))
            if not panel_paths:
                raise ValueError("No panel images available to stitch")

            page_numbers = sorted({int(re.search(r"05_page_(\d+)_panel_\d+\.png$", path.name).group(1)) for path in panel_paths})
            stitched_paths: list[Path] = []
            for page_number in page_numbers:
                stitched_paths.append(_stitch_panel_images_for_page(page_number, version_dir))

            output_status_text.value = f"Stitched {len(stitched_paths)} page image(s)"
            _refresh_all()
        except Exception as exc:
            output_status_text.value = f"Stitching failed: {exc}"
        finally:
            _set_output_busy_state(False)
            page.update()

    async def _run_generate_all_images() -> None:
        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        version_dir = _selected_version_dir[0]

        if not campaign or not episode_slug:
            _set_output_busy_state(False)
            output_status_text.value = "Select a campaign and episode before generating images"
            page.update()
            return

        if version_dir is None or not version_dir.exists():
            _set_output_busy_state(False)
            output_status_text.value = "Select a version before generating images"
            page.update()
            return

        _set_output_busy_state(True)
        output_status_text.value = "Generating images..."
        page.update()
        try:
            config = _build_rerun_config(campaign, episode_slug, "prompt", generate_images=True)
            final_status: list[str] = []

            def _on_event(event: PipelineEventUnion) -> None:
                if isinstance(event, RunCompleted):
                    final_status.append(event.status)
                _refresh_all()
                page.update()

            await services.run_controller.launch_run(config, _on_event)
            _refresh_all()
            output_status_text.value = (
                f"Generated images for {version_dir.name}: "
                f"{final_status[0] if final_status else 'done'}"
            )
        except (RuntimeError, ValueError) as exc:
            output_status_text.value = f"Image generation failed: {exc}"
        finally:
            _set_output_busy_state(False)
            page.update()

    async def on_copy_content(_e: Any) -> None:
        await _set_clipboard(page, preview.value or "", _ft)
        page.update()

    copy_content_button.on_click = on_copy_content

    def on_file_change(_e: Any) -> None:
        _load_selected_file()
        page.update()

    file_list.on_change = on_file_change

    def _refresh_all() -> None:
        _refresh_versions()
        status_value = _set_run_status()
        _sync_settings_controls()
        _set_episode_settings_text()
        _refresh_file_list(status_value)
        _load_selected_file()

        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        version = version_dropdown.value or ""

        campaign_label = campaign or "-"
        episode_label = episode_slug or "-"
        version_label = version or "-"
        output_status_text.value = f"Loaded: {campaign_label} / {episode_label} / {version_label}"

    def on_campaign_changed(event: Any) -> None:
        selected = _extract_change_value(event)
        if selected is not None:
            campaign_dropdown.value = selected
        _set_loading(True)
        page.update()
        _refresh_episodes()
        _refresh_all()
        _set_loading(False)
        page.update()

    def on_episode_changed(event: Any) -> None:
        selected = _extract_change_value(event)
        if selected is not None:
            episode_dropdown.value = selected
        _set_loading(True)
        page.update()
        _refresh_all()
        _set_loading(False)
        page.update()

    def on_version_changed(event: Any) -> None:
        selected = _extract_change_value(event)
        if selected is not None:
            version_dropdown.value = selected
        _set_loading(True)
        page.update()
        status_value = _set_run_status()
        _refresh_file_list(status_value)
        _load_selected_file()
        _set_loading(False)
        page.update()

    _bind_dropdown_handler(campaign_dropdown, on_campaign_changed)
    _bind_dropdown_handler(episode_dropdown, on_episode_changed)
    _bind_dropdown_handler(version_dropdown, on_version_changed)

    def on_open_version(_e: Any) -> None:
        version_dir = _selected_version_dir[0]
        if not version_dir:
            output_status_text.value = "No version selected"
            page.update()
            return
        try:
            subprocess.run(["open", str(version_dir)], check=False)
            output_status_text.value = "Opened version folder"
        except OSError:
            output_status_text.value = "Unable to open version folder"
        page.update()

    def _set_output_busy_state(busy: bool) -> None:
        quick_rerun_button.disabled = busy
        generate_images_button.disabled = busy
        stitch_images_button.disabled = busy
        generate_selected_image_button.disabled = busy

    def on_quick_rerun_stage_changed(_e: Any) -> None:
        if _committed_settings:
            _apply_settings_to_controls(_committed_settings)
        _apply_stage_gating()
        _set_episode_settings_text()
        page.update()

    _bind_dropdown_handler(quick_rerun_stage_dropdown, on_quick_rerun_stage_changed)

    def on_quick_rerun_click(_e: Any) -> None:
        _set_output_busy_state(True)
        output_status_text.value = ""
        page.update()

        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        stage = quick_rerun_stage_dropdown.value or "architect"
        episode = _episodes_by_slug.get(episode_slug)
        url = episode.url if episode and episode.url else ""

        if not campaign or not episode_slug:
            _set_output_busy_state(False)
            output_status_text.value = "Select campaign and episode for quick rerun"
            page.update()
            return

        validation_error = _validate_rerun_settings()
        if validation_error:
            _set_output_busy_state(False)
            output_status_text.value = validation_error
            page.update()
            return

        async def _run_quick_rerun() -> None:
            quick_rerun_gif.visible = True
            quick_rerun_text.visible = True
            output_status_text.value = "Quick rerun started"
            page.update()
            try:
                config = _build_rerun_config(campaign, episode_slug, stage)

                final_status: list[str] = []

                def _on_event(event: PipelineEventUnion) -> None:
                    if event_log is not None:
                        append_pipeline_event(event_log, event, _ft)
                    if isinstance(event, RunCompleted):
                        final_status.append(event.status)
                    else:
                        _refresh_all()
                    page.update()

                await services.run_controller.launch_run(config, _on_event)
                # Refresh AFTER launch_run returns — run_status.json is now written
                _refresh_all()
                output_status_text.value = f"Quick rerun finished: {final_status[0] if final_status else 'done'}"
            except (RuntimeError, ValueError) as exc:
                output_status_text.value = str(exc)
                if event_log is not None:
                    append_log_line(event_log, "Run", str(exc), _ft)
            finally:
                _set_output_busy_state(False)
                quick_rerun_gif.visible = False
                quick_rerun_text.visible = False
                page.update()

        page.run_task(_run_quick_rerun)

    def on_generate_selected_image_click(_e: Any) -> None:
        _set_output_busy_state(True)
        output_status_text.value = ""
        page.update()
        page.run_task(_run_generate_selected_image)

    def on_generate_images_click(_e: Any) -> None:
        _set_output_busy_state(True)
        output_status_text.value = ""
        page.update()
        page.run_task(_run_generate_all_images)

    def on_stitch_images_click(_e: Any) -> None:
        _set_output_busy_state(True)
        output_status_text.value = ""
        page.update()
        page.run_task(_run_stitch_images)

    quick_rerun_button.on_click = on_quick_rerun_click
    generate_selected_image_button.on_click = on_generate_selected_image_click
    generate_images_button.on_click = on_generate_images_click
    stitch_images_button.on_click = on_stitch_images_click

    _refresh_campaign_options()
    _refresh_episodes()
    _refresh_all()

    container = _ft.Column(
        controls=[
            _ft.Text("Output", size=18, weight=_ft.FontWeight.W_600),
            _ft.Row([campaign_dropdown, episode_dropdown, version_dropdown, loading_ring, loading_text], spacing=12),
            _ft.Row(
                controls=[
                    version_path_text,
                    _ft.OutlinedButton("Open Version Folder", on_click=on_open_version),
                ],
                spacing=6,
            ),
            _ft.Row([quick_rerun_stage_dropdown, rerun_only_stage_checkbox], spacing=10),
            _ft.Text("Episode settings", weight=_ft.FontWeight.W_600),
            _ft.Row([
                panel_count_field,
                total_pages_field,
                recap_dropdown,
                aspect_ratio_settings_dropdown,
                generation_mode_dropdown,
                vignette_checkbox,
                art_style_dropdown,
            ], spacing=10),
            _ft.Row([quick_rerun_button, quick_rerun_gif, quick_rerun_text], spacing=10),
            output_status_text,
            settings_text,
            _ft.Text("Run status", weight=_ft.FontWeight.W_600),
            run_status_text,
            _ft.Row(
                controls=[
                    _ft.Container(
                        content=_ft.Column(
                            controls=[
                                _ft.Row(
                                    controls=[
                                        _ft.Text("Files", size=13, weight=_ft.FontWeight.W_500),
                                        generate_selected_image_button,
                                        generate_images_button,
                                        stitch_images_button,
                                    ],
                                    spacing=6,
                                    vertical_alignment=_ft.CrossAxisAlignment.CENTER,
                                ),
                                _ft.Container(
                                    content=file_list,
                                    height=320,
                                    expand=True,
                                    clip_behavior=_ft.ClipBehavior.HARD_EDGE,
                                ),
                            ],
                            spacing=4,
                        ),
                        width=340,
                    ),
                    _ft.Column(
                        controls=[
                            _ft.Row(
                                controls=[copy_content_button],
                                alignment=_ft.MainAxisAlignment.END,
                            ),
                            preview,
                        ],
                        expand=True,
                        spacing=4,
                    ),
                ],
                expand=True,
                spacing=12,
                vertical_alignment=_ft.CrossAxisAlignment.START,
            ),
        ],
        expand=True,
        spacing=8,
    )

    return container, {
        "build_rerun_config": _build_rerun_config,
        "campaign_dropdown": campaign_dropdown,
        "episode_dropdown": episode_dropdown,
        "version_dropdown": version_dropdown,
        "file_list": file_list,
        "preview": preview,
        "copy_content_button": copy_content_button,
        "on_copy_content": on_copy_content,
        "run_status_text": run_status_text,
        "settings_text": settings_text,
        "version_path_text": version_path_text,
        "output_status_text": output_status_text,
        "quick_rerun_stage_dropdown": quick_rerun_stage_dropdown,
        "rerun_only_stage_checkbox": rerun_only_stage_checkbox,
        "quick_rerun_button": quick_rerun_button,
        "generate_images_button": generate_images_button,
        "generate_selected_image_button": generate_selected_image_button,
        "stitch_images_button": stitch_images_button,
        "panel_count_field": panel_count_field,
        "total_pages_field": total_pages_field,
        "recap_dropdown": recap_dropdown,
        "aspect_ratio_dropdown": aspect_ratio_settings_dropdown,
        "generation_mode_dropdown": generation_mode_dropdown,
        "vignette_checkbox": vignette_checkbox,
        "art_style_dropdown": art_style_dropdown,
        "refresh_art_styles": _refresh_art_style_options,
        "refresh_campaigns": _refresh_campaign_options,
        "refresh_episodes": _refresh_episodes,
        "refresh_all": _refresh_all,
    }


def format_log_line(source: str, message: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"{timestamp} [{source}] {message}"


def append_log_line(event_log: Any, source: str, message: str, _ft: Any) -> None:
    line = format_log_line(source, message)
    event_log.controls.append(_ft.Text(line, selectable=True, size=12))
    if len(event_log.controls) > EVENT_LOG_LIMIT:
        event_log.controls[:] = event_log.controls[-EVENT_LOG_LIMIT:]
    latest_line = getattr(event_log, "latest_line_control", None)
    if latest_line is not None:
        latest_line.value = line


def _snippet(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def append_pipeline_event(event_log: Any, event: PipelineEventUnion, _ft: Any) -> None:
    payload = event.to_dict()
    message = payload.get("message") or payload.get("warning") or payload.get("error") or payload["type"]
    phase = payload.get("phase")
    if isinstance(event, RunCompleted):
        source = "Run/completed"
    elif phase:
        source = f"Run/{phase}"
    else:
        source = "Run"

    if isinstance(event, PhaseError):
        detail = event.error or event.message or "unknown error"
        message = f"{_snippet(detail)} See run_status.json for full details."
    elif isinstance(event, PhasePartialFailure):
        detail = event.error_detail or event.message or "partial failure"
        message = f"{_snippet(detail)} See run_status.json for full details."
    elif isinstance(event, RunCompleted) and event.status != "ok":
        detail = event.error_messages[0] if event.error_messages else "Run completed with errors"
        message = f"{_snippet(detail)} See run_status.json for full details."

    append_log_line(event_log, source, str(message), _ft)


def open_settings_dialog(page: Any, dialog: Any) -> None:
    show_dialog = getattr(page, "show_dialog", None)
    if callable(show_dialog):
        show_dialog(dialog)
    else:
        page.dialog = dialog
        dialog.open = True
        page.update()


def close_settings_dialog(page: Any, dialog: Any) -> None:
    pop_dialog = getattr(page, "pop_dialog", None)
    if callable(pop_dialog):
        pop_dialog()
    else:
        dialog.open = False
        page.update()


def build_main_layout(page: Any, services: AppServices) -> dict[str, Any]:
    if ft is None:
        raise RuntimeError("flet is not installed. Install flet to use the GUI.")

    page.title = "TTRPG Comic Generator"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 16

    window = getattr(page, "window", None)
    if window is not None:
        window.width = 1500
        window.height = 1100
        window.min_width = 1200
        window.min_height = 900
    else:
        page.window_width = 1500
        page.window_height = 1100
        page.window_min_width = 1200
        page.window_min_height = 900

    page.update()

    preflight_warnings = _playwright_preflight_warnings()
    preflight_text = ft.Text(
        "\n".join(preflight_warnings),
        color=ft.Colors.AMBER_900,
        size=12,
        visible=bool(preflight_warnings),
        selectable=True,
    )

    event_log = ft.ListView(expand=True, auto_scroll=True, spacing=4, height=180)
    latest_log_line = ft.Text("No events yet", size=12, selectable=True)
    setattr(event_log, "latest_line_control", latest_log_line)
    event_log_container = ft.Container(content=event_log, height=180, visible=False)
    event_log_toggle_label = ft.Text("Show Event Log")
    event_log_toggle = ft.TextButton(content=event_log_toggle_label)

    def toggle_event_log(_e: Any) -> None:
        event_log_container.visible = not event_log_container.visible
        event_log_toggle_label.value = (
            "Hide Event Log" if event_log_container.visible else "Show Event Log"
        )
        page.update()

    event_log_toggle.on_click = toggle_event_log

    gemini_key_input = ft.TextField(
        label="Gemini API Key",
        value=services.settings.get_gemini_api_key() or "",
        password=True,
        can_reveal_password=True,
        expand=True,
    )
    default_model_input = ft.TextField(
        label="Default Model",
        value=services.settings.get_default_model(),
        expand=True,
    )
    image_generation_model_input = ft.Dropdown(
        label="Image Generation Model",
        value=services.settings.get_image_generation_model(),
        options=[
            ft.dropdown.Option("gemini-2.5-flash-image", "gemini-2.5-flash-image"),
            ft.dropdown.Option("gemini-3.1-flash-image", "gemini-3.1-flash-image"),
            ft.dropdown.Option("gemini-3-pro-image", "gemini-3-pro-image"),
        ],
        width=420,
    )

    status_text = ft.Text("Ready", size=12)

    def on_save_settings(_event: Any) -> None:
        if gemini_key_input.value:
            services.settings.set_gemini_api_key(gemini_key_input.value)
        services.settings.set_default_model(default_model_input.value or "")
        services.settings.set_image_generation_model(image_generation_model_input.value or "gemini-2.5-flash-image")
        services.settings.apply_to_environment()
        status_text.value = "Settings saved"
        append_log_line(event_log, "Settings", "Saved settings", ft)
        page.update()

    settings_dialog = ft.AlertDialog(
        modal=False,
        title=ft.Text("Settings"),
        content=ft.Column(
            controls=[gemini_key_input, default_model_input, image_generation_model_input],
            tight=True,
            width=520,
        ),
        actions=[
            ft.TextButton("Close", on_click=lambda _e: close_settings_dialog(page, settings_dialog)),
            ft.FilledButton("Save", on_click=on_save_settings),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    settings_button = ft.TextButton(
        "⚙ Settings",
        on_click=lambda _e: open_settings_dialog(page, settings_dialog),
    )

    prompt_view, prompt_page_state = build_prompt_page(services, page, ft)
    output_view, output_page_state = build_output_page(services, page, ft, event_log)

    def _sync_output_to_version_dir(version_dir_value: str) -> None:
        version_path = Path(version_dir_value)
        try:
            version = version_path.name
            episode_slug = version_path.parent.name
            campaign = version_path.parent.parent.name
        except Exception:
            return
        output_page_state["refresh_campaigns"](campaign)
        output_page_state["campaign_dropdown"].value = campaign
        output_page_state["refresh_episodes"]()
        output_page_state["episode_dropdown"].value = episode_slug
        output_page_state["version_dropdown"].value = version
        output_page_state["refresh_all"]()

    def _on_campaign_created(new_campaign: str) -> None:
        prompt_page_state["refresh_campaigns"](new_campaign)
        output_page_state["refresh_campaigns"](new_campaign)
        output_page_state["refresh_all"]()
        page.update()

    def _on_run_finished(version_dir: str | None) -> None:
        if version_dir:
            _sync_output_to_version_dir(version_dir)
        else:
            output_page_state["refresh_all"]()
        page.update()

    run_view, run_page_state = build_run_page(
        services,
        page,
        event_log,
        ft,
        on_campaign_created=_on_campaign_created,
        on_run_finished=_on_run_finished,
    )

    def _refresh_art_style_selectors() -> None:
        """Rebuild Run/Output art style options after Prompts saves or tab switch."""
        preferred_run = run_page_state["art_style_dropdown"].value
        run_page_state["refresh_art_styles"](preferred_run)
        # Preserve Output selection when possible; also pick up new campaign overrides.
        preferred_out = output_page_state["art_style_dropdown"].value
        output_page_state["refresh_art_styles"](preferred_out)

    prompt_page_state["set_on_art_styles_changed"](_refresh_art_style_selectors)

    prompt_view.visible = False
    output_view.visible = False

    def set_workspace(name: str) -> None:
        run_view.visible = name == "Run"
        prompt_view.visible = name == "Prompts"
        output_view.visible = name == "Output"
        if name == "Prompts":
            prompt_page_state["refresh_file_list"]()
        elif name in {"Run", "Output"}:
            _refresh_art_style_selectors()
            if name == "Output":
                # Re-sync settings (including preferred art_style from version) from disk.
                output_page_state["refresh_all"]()
        append_log_line(event_log, "UI", f"Switched to {name}", ft)
        page.update()

    nav_row = ft.Row(
        controls=[
            ft.TextButton("Run", on_click=lambda _e: set_workspace("Run")),
            ft.TextButton("Prompts", on_click=lambda _e: set_workspace("Prompts")),
            ft.TextButton("Output", on_click=lambda _e: set_workspace("Output")),
        ],
        spacing=8,
    )

    app_content = ft.Column(
        controls=[
            ft.Row(
                controls=[ft.Text("TTRPG Comic Generator", size=20, weight=ft.FontWeight.W_700), settings_button],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            preflight_text,
            nav_row,
            run_view,
            prompt_view,
            output_view,
            ft.Divider(),
            ft.Text("Latest Event", weight=ft.FontWeight.W_600),
            latest_log_line,
            event_log_toggle,
            event_log_container,
            status_text,
        ],
        expand=True,
    )

    page.add(app_content)
    append_log_line(event_log, "System", "GUI initialized", ft)
    page.update()

    return {
        "navigation": nav_row,
        "run_view": run_view,
        "run_page_state": run_page_state,
        "prompt_view": prompt_view,
        "prompt_page_state": prompt_page_state,
        "output_view": output_view,
        "output_page_state": output_page_state,
        "event_log": event_log,
        "settings_button": settings_button,
        "settings_dialog": settings_dialog,
        "status_text": status_text,
    }


def main(page: Any) -> None:
    services = create_services()
    build_main_layout(page, services)


def run() -> None:
    if ft is None:
        raise RuntimeError("flet is not installed. Install flet to run src/gui.py.")
    ft.run(main)


if __name__ == "__main__":
    run()