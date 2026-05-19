"""Flet GUI shell for TTRPG Comic Generator."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from model_defaults import DEFAULT_MODEL
from pipeline_config import RunConfig
from pipeline_events import (
    PhaseError,
    PhasePartialFailure,
    PhaseStarted,
    RunCompleted,
    PipelineEventUnion,
)
from prompt_templates import DEFAULT_PROMPTS_DIR
from prompter import ART_DIRECTION_TEMPLATE_FIELDS, ART_DIRECTION_TEMPLATE_FILENAME
from repository_service import CampaignPrompts, RepositoryService
from run_controller import RunController
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
    ("beater", "Beater"),
    ("script", "Script"),
    ("style", "Style"),
    ("prompt", "Prompt"),
]


@dataclass(frozen=True)
class AppServices:
    repository: RepositoryService
    settings: SettingsService
    run_controller: RunController


def create_services(campaigns_root: Path = Path("campaigns")) -> AppServices:
    return AppServices(
        repository=RepositoryService(campaigns_root),
        settings=SettingsService(),
        run_controller=RunController(),
    )


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
    panel_count_field = _ft.TextField(label="Panels", value="6", width=80)
    total_pages_field = _ft.TextField(label="Pages", value="1", width=80)
    model_field = _ft.TextField(
        label="Model",
        value=services.settings.get_default_model(),
        width=280,
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
            rerun_dropdown.value = "beater"

    def _build_config() -> RunConfig:
        mode = run_mode_dropdown.value or "story_url"
        selected_episode = _episodes_by_slug.get(episode_dropdown.value or "")
        if mode == "story_url":
            url = url_field.value or ""
            rerun = "scrape"
        else:
            url = selected_episode.url if selected_episode and selected_episode.url else (url_field.value or "")
            rerun = rerun_dropdown.value or "beater"
        model = model_field.value or DEFAULT_MODEL
        return RunConfig(
            url=url,
            campaign=campaign_dropdown.value or "",
            rerun_from=rerun,
            recap_version=recap_dropdown.value or "standard",  # type: ignore[arg-type]
            skip_style=bool(skip_style_checkbox.value),
            panel_count=int(panel_count_field.value or 6),
            total_pages=int(total_pages_field.value or 1),
            beater_model=model,
            script_model=model,
            style_model=model,
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
            run_button.disabled = False
            running_ring.visible = False
            running_gif.visible = False
            running_text.visible = False
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
            if callable(on_run_finished):
                on_run_finished()
        append_pipeline_event(event_log, event, _ft)
        page.update()

    async def _execute_run() -> None:
        try:
            config = _build_config()
            task = services.run_controller.launch_run(config, on_pipeline_event)
            await task
        except (RuntimeError, ValueError) as exc:
            status_summary.value = f"✗ {exc}"
            run_error_text.value = str(exc)
            append_log_line(event_log, "Run", str(exc), _ft)
            run_button.disabled = False
            running_ring.visible = False
            running_gif.visible = False
            running_text.visible = False
            page.update()
        finally:
            run_button.disabled = False
            running_ring.visible = False
            running_gif.visible = False
            running_text.visible = False
            page.update()

    def on_run_click(_event: Any) -> None:
        status_summary.value = ""
        run_error_text.value = ""
        run_button.disabled = True
        running_ring.visible = True
        running_gif.visible = True
        running_text.visible = True
        page.update()
        page.run_task(_execute_run)

    def on_campaign_changed(event: Any) -> None:
        selected = _extract_change_value(event)
        if selected is not None:
            campaign_dropdown.value = selected
        _refresh_episode_options()
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
    _sync_mode_controls()

    container = _ft.Column(
        controls=[
            _ft.Text("Run", size=18, weight=_ft.FontWeight.W_600),
            _ft.Row([campaign_dropdown, new_campaign_field, campaign_add_button], spacing=12),
            campaign_status_text,
            _ft.Row([run_mode_dropdown, url_field, episode_dropdown], spacing=12),
            _ft.Row([rerun_dropdown, recap_dropdown, skip_style_checkbox], spacing=12),
            _ft.Row([panel_count_field, total_pages_field, model_field], spacing=12),
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
        "panel_count_field": panel_count_field,
        "total_pages_field": total_pages_field,
        "model_field": model_field,
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
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    if not isinstance(obj, dict):
        return "Template must be a JSON object"
    missing = [
        f"{name} ({label})"
        for name, label in ART_DIRECTION_TEMPLATE_FIELDS
        if not isinstance(obj.get(name), str) or not obj[name].strip()
    ]
    if missing:
        return "Missing fields: " + ", ".join(missing)
    return None


_PROMPT_FILE_LABELS: list[tuple[str, str]] = [
    ("art_direction_template", ART_DIRECTION_TEMPLATE_FILENAME),
    ("master_beater_system", "master_beater_system.txt"),
    ("master_beater_user", "master_beater_user.txt"),
    ("scriptwriter_system", "scriptwriter_system.txt"),
    ("scriptwriter_user", "scriptwriter_user.txt"),
    ("style_integrator_system", "style_integrator_system.txt"),
    ("style_integrator_user", "style_integrator_user.txt"),
    ("page_prompt", "page_prompt.txt"),
]


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
        content=_ft.Column(spacing=2)
    )

    editor = _ft.TextField(
        multiline=True,
        min_lines=20,
        max_lines=40,
        expand=True,
        text_style=_ft.TextStyle(font_family="monospace", size=12),
    )

    validation_text = _ft.Text("", color=_ft.Colors.RED_700, size=12)
    capture_preview_text = _ft.Text("Next run will capture: all campaign prompts + art template", size=12)

    # Tracks which field key is selected (e.g. "art_direction_template")
    _selected_key: list[str] = [""]
    # Maps field key -> resolved Path from current campaign prompts
    _paths: dict[str, Path] = {}

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

    def _default_prompt_path_for_key(key: str) -> Path | None:
        filename = next((fn for k, fn in _PROMPT_FILE_LABELS if k == key), None)
        if not filename:
            return None
        return DEFAULT_PROMPTS_DIR / filename

    def _get_prompts() -> CampaignPrompts | None:
        campaign = campaign_dropdown.value
        if not campaign:
            return None
        return services.repository.get_campaign_prompts(campaign)

    def _refresh_file_list() -> None:
        prompts = _get_prompts()
        rows = file_list.content
        rows.controls.clear()
        _paths.clear()
        if prompts is None:
            return
        for key, filename in _PROMPT_FILE_LABELS:
            path: Path = getattr(prompts, key)
            _paths[key] = path
            exists_mark = "" if path.exists() else " ✗"
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
        campaign_path = _paths.get(key)
        source_path: Path | None = campaign_path
        if not source_path or not source_path.exists():
            source_path = _default_prompt_path_for_key(key)
        validation_text.value = ""
        editor.border_color = None
        if not source_path or not source_path.exists():
            editor.value = ""
            return
        editor.value = source_path.read_text(encoding="utf-8")

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
        path = _paths.get(key)
        if not path:
            return
        text = editor.value or ""
        if key == "art_direction_template":
            err = _validate_art_template(text)
            if err:
                validation_text.value = err
                editor.border_color = _ft.Colors.RED_700
                page.update()
                return
        validation_text.value = ""
        editor.border_color = None
        path.write_text(text, encoding="utf-8")
        _refresh_file_list()
        page.update()

    def on_reset(_e: Any) -> None:
        key = _selected_key[0]
        if not key:
            return
        filename = next((fn for k, fn in _PROMPT_FILE_LABELS if k == key), None)
        if not filename:
            return
        default_path = DEFAULT_PROMPTS_DIR / filename
        if not default_path.exists():
            return
        editor.value = default_path.read_text(encoding="utf-8")
        validation_text.value = ""
        editor.border_color = None
        page.update()

    def on_campaign_changed(event: Any) -> None:
        selected = _extract_change_value(event)
        if selected is not None:
            campaign_dropdown.value = selected
        _set_loading(True)
        page.update()
        _selected_key[0] = ""
        editor.value = ""
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
                    _ft.Container(
                        content=_ft.Column(
                            controls=[
                                _ft.Text("Files", size=13, weight=_ft.FontWeight.W_500),
                                file_list,
                            ],
                            spacing=4,
                        ),
                        width=240,
                    ),
                    _ft.Column(
                        controls=[
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

    return container, {
        "campaign_dropdown": campaign_dropdown,
        "file_list": file_list,
        "editor": editor,
        "validation_text": validation_text,
        "capture_preview_text": capture_preview_text,
        "on_save": on_save,
        "on_load": on_load,
        "on_reset": on_reset,
        "refresh_file_list": _refresh_file_list,
        "refresh_campaigns": _refresh_campaign_options,
        "selected_key": _selected_key,
        "paths": _paths,
    }


def _format_preview(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".json":
        return text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(parsed, indent=2, ensure_ascii=True)


def _safe_set_clipboard(page: Any, text: str) -> None:
    setter = getattr(page, "set_clipboard", None)
    if callable(setter):
        setter(text)


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
        label="Quick Rerun Stage",
        value="beater",
        options=[_ft.dropdown.Option(stage, label) for stage, label in _STAGE_LABELS],
        width=190,
    )
    quick_rerun_button = _ft.OutlinedButton("Quick Rerun")
    quick_rerun_gif = _ft.Image(src=LOADING_GIF_URL, width=20, height=20, visible=False)
    quick_rerun_text = _ft.Text("Running...", size=12, visible=False)

    file_list = _ft.RadioGroup(content=_ft.Column(spacing=2))
    preview = _ft.TextField(
        multiline=True,
        min_lines=20,
        max_lines=40,
        read_only=True,
        expand=True,
        text_style=_ft.TextStyle(font_family="monospace", size=12),
    )

    run_status_text = _ft.Text("", size=12, selectable=True)
    version_path_text = _ft.Text("", size=11, selectable=True)
    output_status_text = _ft.Text("", size=12)

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
        version_dropdown.options = [_ft.dropdown.Option(v.version) for v in versions]
        version_dropdown.value = versions[-1].version if versions else None

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
            "02_5_story_bible.json",
            "03_script.json",
            "03_5_styled_script.json",
            "04_page_prompt.txt",
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

        preferred_default = "run_status.json" if status_value and status_value != "ok" else "04_page_prompt.txt"
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
            return
        path = _selected_files.get(selected)
        if not path or not path.exists():
            preview.value = ""
            return
        preview.value = _format_preview(path)

    def on_file_change(_e: Any) -> None:
        _load_selected_file()
        page.update()

    file_list.on_change = on_file_change

    def _refresh_all() -> None:
        _refresh_versions()
        status_value = _set_run_status()
        _refresh_file_list(status_value)
        _load_selected_file()
        campaign = campaign_dropdown.value or "-"
        episode = episode_dropdown.value or "-"
        version = version_dropdown.value or "-"
        output_status_text.value = f"Loaded: {campaign} / {episode} / {version}"

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

    def on_copy_prompt_path(_e: Any) -> None:
        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        version = version_dropdown.value or ""
        if not (campaign and episode_slug and version):
            return
        page_prompt = services.repository.get_version_files(campaign, episode_slug, version).page_prompt
        if not page_prompt:
            output_status_text.value = "No page prompt file for selected version"
        else:
            _safe_set_clipboard(page, str(page_prompt))
            output_status_text.value = "Copied latest prompt path"
        page.update()

    def on_copy_script_path(_e: Any) -> None:
        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        version = version_dropdown.value or ""
        if not (campaign and episode_slug and version):
            return
        script = services.repository.get_version_files(campaign, episode_slug, version).script
        if not script:
            output_status_text.value = "No script file for selected version"
        else:
            _safe_set_clipboard(page, str(script))
            output_status_text.value = "Copied latest script path"
        page.update()

    def on_quick_rerun_click(_e: Any) -> None:
        campaign = campaign_dropdown.value or ""
        episode_slug = episode_dropdown.value or ""
        stage = quick_rerun_stage_dropdown.value or "beater"
        episode = _episodes_by_slug.get(episode_slug)
        url = episode.url if episode and episode.url else ""

        if not campaign or not episode_slug:
            output_status_text.value = "Select campaign and episode for quick rerun"
            page.update()
            return

        async def _run_quick_rerun() -> None:
            quick_rerun_button.disabled = True
            quick_rerun_gif.visible = True
            quick_rerun_text.visible = True
            output_status_text.value = "Quick rerun started"
            page.update()
            try:
                model = services.settings.get_default_model() or DEFAULT_MODEL
                config = RunConfig(
                    url=url,
                    campaign=campaign,
                    rerun_from=stage,  # type: ignore[arg-type]
                    recap_version="standard",
                    skip_style=False,
                    panel_count=6,
                    total_pages=1,
                    beater_model=model,
                    script_model=model,
                    style_model=model,
                )

                def _on_event(event: PipelineEventUnion) -> None:
                    if event_log is not None:
                        append_pipeline_event(event_log, event, _ft)
                    if isinstance(event, RunCompleted):
                        output_status_text.value = f"Quick rerun finished: {event.status}"
                        _refresh_all()
                    page.update()

                await services.run_controller.launch_run(config, _on_event)
            except (RuntimeError, ValueError) as exc:
                output_status_text.value = str(exc)
                if event_log is not None:
                    append_log_line(event_log, "Run", str(exc), _ft)
            finally:
                quick_rerun_button.disabled = False
                quick_rerun_gif.visible = False
                quick_rerun_text.visible = False
                page.update()

        page.run_task(_run_quick_rerun)

    quick_rerun_button.on_click = on_quick_rerun_click

    _refresh_campaign_options()
    _refresh_episodes()
    _refresh_all()

    container = _ft.Column(
        controls=[
            _ft.Text("Output", size=18, weight=_ft.FontWeight.W_600),
            _ft.Row([campaign_dropdown, episode_dropdown, version_dropdown, loading_ring, loading_text], spacing=12),
            version_path_text,
            _ft.Row(
                controls=[
                    _ft.OutlinedButton("Open Version Folder", on_click=on_open_version),
                    _ft.OutlinedButton("Copy Latest Prompt Path", on_click=on_copy_prompt_path),
                    _ft.OutlinedButton("Copy Latest Script Path", on_click=on_copy_script_path),
                ],
                spacing=8,
            ),
            _ft.Row([quick_rerun_stage_dropdown, quick_rerun_button, quick_rerun_gif, quick_rerun_text], spacing=10),
            output_status_text,
            _ft.Text("Run status", weight=_ft.FontWeight.W_600),
            run_status_text,
            _ft.Row(
                controls=[
                    _ft.Container(
                        content=_ft.Column(
                            controls=[
                                _ft.Text("Files", size=13, weight=_ft.FontWeight.W_500),
                                file_list,
                            ],
                            spacing=4,
                        ),
                        width=300,
                    ),
                    _ft.Column(
                        controls=[preview],
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
        "campaign_dropdown": campaign_dropdown,
        "episode_dropdown": episode_dropdown,
        "version_dropdown": version_dropdown,
        "file_list": file_list,
        "preview": preview,
        "run_status_text": run_status_text,
        "version_path_text": version_path_text,
        "output_status_text": output_status_text,
        "quick_rerun_stage_dropdown": quick_rerun_stage_dropdown,
        "quick_rerun_button": quick_rerun_button,
        "refresh_campaigns": _refresh_campaign_options,
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
    ollama_url_input = ft.TextField(
        label="Ollama Base URL",
        value=services.settings.get_ollama_base_url(),
        expand=True,
    )
    default_model_input = ft.TextField(
        label="Default Model",
        value=services.settings.get_default_model(),
        expand=True,
    )
    backend_dropdown = ft.Dropdown(
        label="Backend",
        value="gemini" if services.settings.get_default_model().startswith("gemini-") else "ollama",
        options=[
            ft.dropdown.Option("gemini", "Gemini"),
            ft.dropdown.Option("ollama", "Ollama"),
        ],
    )

    status_text = ft.Text("Ready", size=12)

    def on_save_settings(_event: Any) -> None:
        if gemini_key_input.value:
            services.settings.set_gemini_api_key(gemini_key_input.value)
        services.settings.set_ollama_base_url(ollama_url_input.value or "")
        services.settings.set_default_model(default_model_input.value or "")
        services.settings.apply_to_environment()
        status_text.value = "Settings saved"
        append_log_line(event_log, "Settings", "Saved settings", ft)
        page.update()

    settings_dialog = ft.AlertDialog(
        modal=False,
        title=ft.Text("Settings"),
        content=ft.Column(
            controls=[gemini_key_input, ollama_url_input, default_model_input, backend_dropdown],
            tight=True,
            width=520,
        ),
        actions=[
            ft.TextButton("Close", on_click=lambda _e: close_settings_dialog(page, settings_dialog)),
            ft.FilledButton("Save", on_click=on_save_settings),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    settings_button = ft.IconButton(
        icon=ft.Icons.SETTINGS,
        tooltip="Settings",
        on_click=lambda _e: open_settings_dialog(page, settings_dialog),
    )

    prompt_view, prompt_page_state = build_prompt_page(services, page, ft)
    output_view, output_page_state = build_output_page(services, page, ft, event_log)

    def _on_campaign_created(new_campaign: str) -> None:
        prompt_page_state["refresh_campaigns"](new_campaign)
        output_page_state["refresh_campaigns"](new_campaign)
        output_page_state["refresh_all"]()

    def _on_run_finished() -> None:
        output_page_state["refresh_all"]()

    run_view, run_page_state = build_run_page(
        services,
        page,
        event_log,
        ft,
        on_campaign_created=_on_campaign_created,
        on_run_finished=_on_run_finished,
    )
    prompt_view.visible = False
    output_view.visible = False

    def set_workspace(name: str) -> None:
        run_view.visible = name == "Run"
        prompt_view.visible = name == "Prompts"
        output_view.visible = name == "Output"
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