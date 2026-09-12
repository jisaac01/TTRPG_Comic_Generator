"""Tests for the Phase 4 Run workspace (build_run_page)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("flet")

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft

from gui import AppServices, build_run_page
from pipeline_events import PhaseStarted, RunCompleted
from repository_service import Episode
from run_controller import RunResult


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self) -> None:
        self.update_calls = 0
        self._last_task_coro: Any = None

    def update(self) -> None:
        self.update_calls += 1

    def run_task(self, coro_func: Any, *args: Any, **kwargs: Any) -> None:
        self._last_task_coro = coro_func


class _FakeRunController:
    def __init__(self) -> None:
        self.launched_config: Any = None
        self._callback: Any = None
        self._completion_status: str = "ok"

    def launch_run(self, config: Any, event_callback: Any) -> "asyncio.Task[RunResult]":
        self.launched_config = config
        self._callback = event_callback

        async def _complete() -> RunResult:
            event_callback(
                RunCompleted(
                    version="v001",
                    version_dir="campaigns/flail/ep-1/v001",
                    status=self._completion_status,  # type: ignore[arg-type]
                    failed_phases=[],
                    error_messages=[],
                )
            )
            return RunResult(
                status=self._completion_status,  # type: ignore[arg-type]
                version="v001",
                version_dir="campaigns/flail/ep-1/v001",
                failed_phases=[],
                errors=[],
                error_details=[],
                events=[],
                output=None,
            )

        return asyncio.ensure_future(_complete())

    def current_run(self) -> None:
        return None

    def last_result(self) -> None:
        return None


class _FailingRunController:
    def launch_run(self, config: Any, event_callback: Any) -> "asyncio.Task[RunResult]":
        async def _complete() -> RunResult:
            return RunResult(
                status="failed",
                version="v001",
                version_dir="campaigns/flail/ep-1/v001",
                failed_phases=["scrape"],
                errors=["scrape failed"],
                error_details=["browserType.launch: Executable doesn't exist"],
                events=[],
                output=None,
            )

        return asyncio.ensure_future(_complete())

    def current_run(self) -> None:
        return None

    def last_result(self) -> None:
        return None


class _FakeSettingsService:
    def get_default_model(self) -> str:
        return "gemini-3.1-flash-lite"

    def get_gemini_api_key(self) -> str | None:
        return None

    def get_ollama_base_url(self) -> str:
        return "http://localhost:11434/v1"

    def get_image_generation_model(self) -> str:
        return "gemini-3.1-flash-image"

    def set_image_generation_model(self, model: str) -> None:
        self._image_generation_model = model


class _FakeRepositoryService:
    def __init__(self) -> None:
        self._campaigns = ["flail", "kingmaker"]
        self.campaigns_root = Path("campaigns")

    def list_campaigns(self) -> list[str]:
        return list(self._campaigns)

    def list_episodes(self, campaign: str) -> list[Episode]:
        if campaign != "flail":
            return []
        return [
            Episode(
                campaign="flail",
                slug="ep-1",
                url="https://example.com/flail-ep-1",
                title="Episode 1",
                created_at="2026-05-18T00:00:00Z",
                path=Path("campaigns/flail/ep-1"),
            )
        ]

    def create_campaign(self, campaign: str) -> Path:
        name = campaign.strip()
        if not name:
            raise ValueError("campaign name cannot be empty")
        if name in self._campaigns:
            raise FileExistsError(name)
        self._campaigns.append(name)
        return Path("campaigns") / name


def _services(fake_rc: Any | None = None) -> AppServices:
    from repository_service import RepositoryService

    return AppServices(
        repository=_FakeRepositoryService(),  # type: ignore[arg-type]
        settings=_FakeSettingsService(),  # type: ignore[arg-type]
        run_controller=fake_rc or _FakeRunController(),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_page_campaign_dropdown_populated() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    dropdown = state["campaign_dropdown"]
    option_keys = [o.key for o in dropdown.options]
    assert option_keys == ["flail", "kingmaker"]


def test_run_page_aspect_ratio_dropdown_uses_orientation_names() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    dropdown = state["aspect_ratio_dropdown"]
    labels = {o.key: o.text for o in dropdown.options}
    assert labels["1:1"] == "1:1 — Square"
    assert labels["4:3"] == "4:3 — Vertical"
    assert labels["3:2"] == "3:2 — Horizontal"
    assert dropdown.value == "3:2"


def test_run_page_exposes_generation_mode_selector() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    assert "generation_mode_dropdown" in state
    assert state["generation_mode_dropdown"].value == "page"


def test_run_page_exposes_vignette_checkbox() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    assert "vignette_checkbox" in state
    assert state["vignette_checkbox"].value is False


def test_run_page_exposes_cache_buster_checkbox_on_by_default() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    assert "cache_buster_checkbox" in state
    assert state["cache_buster_checkbox"].label == "Add cache buster"
    assert state["cache_buster_checkbox"].value is True
    assert state["build_config"]().cache_buster is True


def test_run_page_exposes_feature_toggle_checkboxes_off_by_default() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    assert state["unstyled_prompts_checkbox"].label == "Output un-styled prompts"
    assert state["unstyled_prompts_checkbox"].value is True
    assert state["chat_mode_checkbox"].label == "Chat mode"
    assert state["chat_mode_checkbox"].value is False
    assert state["pg13_mode_checkbox"].label == "PG-13 mode"
    assert state["pg13_mode_checkbox"].value is False
    config = state["build_config"]()
    assert config.unstyled_prompts is True
    assert config.chat_mode is False
    assert config.pg13_mode is False
    assert "skip_style_checkbox" not in state
    assert not hasattr(config, "skip_style")


def test_run_page_places_toggles_on_row_before_run_button() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    container, state = build_run_page(_services(), page, event_log, ft)

    toggles = [
        state["generate_images_checkbox"],
        state["vignette_checkbox"],
        state["cache_buster_checkbox"],
        state["unstyled_prompts_checkbox"],
        state["chat_mode_checkbox"],
        state["pg13_mode_checkbox"],
    ]
    rows = [control for control in container.controls if hasattr(control, "controls")]
    toggle_row = next(row for row in rows if all(toggle in row.controls for toggle in toggles))
    settings_row = next(
        row
        for row in rows
        if state["panel_count_field"] in row.controls and state["total_pages_field"] in row.controls
    )
    run_row = next(row for row in rows if state["run_button"] in row.controls)

    assert state["rerun_dropdown"] not in toggle_row.controls
    assert state["panel_count_field"] not in toggle_row.controls
    assert state["vignette_checkbox"] not in settings_row.controls
    assert container.controls.index(settings_row) < container.controls.index(toggle_row)
    assert container.controls.index(toggle_row) < container.controls.index(run_row)


def test_run_page_exposes_art_style_selector() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    assert "art_style_dropdown" in state
    options = state["art_style_dropdown"].options
    assert options
    assert any(o.key == "bundled:brutalist" for o in options)
    labels = {o.key: o.text for o in options}
    assert labels["bundled:brutalist"] == "brutalist (bundled)"
    assert state["art_style_dropdown"].value == "bundled:brutalist"


def test_run_page_build_config_maps_form_fields() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["url_field"].value = "https://example.com/story"
    state["campaign_dropdown"].value = "flail"
    state["run_mode_dropdown"].value = "existing_episode"
    state["episode_dropdown"].value = "ep-1"
    state["rerun_dropdown"].value = "script"
    state["recap_dropdown"].value = "short"
    state["generate_images_checkbox"].value = True
    state["panel_count_field"].value = "4"
    state["total_pages_field"].value = "2"
    state["aspect_ratio_dropdown"].value = "3:2"
    state["generation_mode_dropdown"].value = "panel"
    state["vignette_checkbox"].value = True
    state["cache_buster_checkbox"].value = False
    state["unstyled_prompts_checkbox"].value = True
    state["chat_mode_checkbox"].value = True
    state["pg13_mode_checkbox"].value = True
    state["art_style_dropdown"].value = "bundled:brutalist"

    config = state["build_config"]()
    assert config.url == "https://example.com/flail-ep-1"
    assert config.campaign == "flail"
    assert config.rerun_from == "script"
    assert config.recap_version == "short"
    assert config.panel_count == 4
    assert config.total_pages == 2
    assert config.aspect_ratio == "3:2"
    assert config.generation_mode == "panel"
    assert config.vignette is True
    assert config.cache_buster is False
    assert config.unstyled_prompts is True
    assert config.chat_mode is True
    assert config.pg13_mode is True
    assert config.art_style == "bundled:brutalist"
    assert config.generate_images is True
    assert config.image_generation_model == "gemini-3.1-flash-image"
    assert not hasattr(config, "architect_model")
    assert not hasattr(config, "script_model")
    assert not hasattr(config, "style_model")


def test_run_page_build_config_story_url_defaults_to_scrape() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["run_mode_dropdown"].value = "story_url"
    state["url_field"].value = "https://example.com/story"
    config = state["build_config"]()
    assert config.rerun_from == "scrape"


def test_run_page_on_pipeline_event_updates_phase_badge() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["on_pipeline_event"](PhaseStarted(phase="script", message="Writing script..."))

    assert "Stage: script" in state["phase_badge"].value
    assert page.update_calls >= 1


def test_run_page_on_pipeline_event_run_completed_ok_updates_status() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["on_pipeline_event"](
        RunCompleted(status="ok", version="v001", version_dir="campaigns/flail/ep/v001")
    )

    assert state["status_summary"].value == "✓ OK"
    assert state["run_button"].disabled is False
    assert "v001" in state["version_text"].value


def test_run_page_on_pipeline_event_appends_to_event_log() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    initial_count = len(event_log.controls)
    state["on_pipeline_event"](PhaseStarted(phase="scrape", message="Scraping..."))
    assert len(event_log.controls) == initial_count + 1



def test_run_page_run_button_disabled_on_click() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    fake_rc = _FakeRunController()
    _container, state = build_run_page(_services(fake_rc), page, event_log, ft)

    assert state["run_button"].disabled is False

    state["run_button"].on_click(None)

    assert state["run_button"].disabled is True
    assert page._last_task_coro is state["execute_run"]


def test_run_page_does_not_expose_model_controls() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    assert "model_field" not in state
    assert "image_generation_model_dropdown" not in state


@pytest.mark.asyncio
async def test_run_page_execute_run_calls_launch_with_correct_config() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    fake_rc = _FakeRunController()
    _container, state = build_run_page(_services(fake_rc), page, event_log, ft)

    state["url_field"].value = "https://example.com/story"
    state["campaign_dropdown"].value = "flail"
    state["run_mode_dropdown"].value = "story_url"

    await state["execute_run"]()

    assert fake_rc.launched_config is not None
    assert fake_rc.launched_config.url == "https://example.com/story"
    assert fake_rc.launched_config.campaign == "flail"


def test_run_page_add_campaign_updates_dropdown() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["new_campaign_field"].value = "new-world"
    state["campaign_add_button"].on_click(None)

    option_keys = [o.key for o in state["campaign_dropdown"].options]
    assert "new-world" in option_keys
    assert state["campaign_dropdown"].value == "new-world"


@pytest.mark.asyncio
async def test_run_page_execute_run_reenables_button_on_completion() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    fake_rc = _FakeRunController()
    _container, state = build_run_page(_services(fake_rc), page, event_log, ft)

    state["run_button"].disabled = True  # simulate mid-run state
    await state["execute_run"]()

    assert state["run_button"].disabled is False
    assert state["status_summary"].value == "✓ OK"


@pytest.mark.asyncio
async def test_run_page_execute_run_surfaces_failed_result_without_event() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(_FailingRunController()), page, event_log, ft)

    await state["execute_run"]()

    assert state["status_summary"].value == "✗ Failed"
    assert "browserType.launch" in state["run_error_text"].value


@pytest.mark.asyncio
async def test_run_page_execute_run_invokes_on_run_finished_after_task() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    fake_rc = _FakeRunController()
    received_version_dirs: list[str | None] = []

    def _on_finished(version_dir: str | None) -> None:
        received_version_dirs.append(version_dir)

    _container, state = build_run_page(
        _services(fake_rc),
        page,
        event_log,
        ft,
        on_run_finished=_on_finished,
    )

    await state["execute_run"]()

    assert len(received_version_dirs) == 1
