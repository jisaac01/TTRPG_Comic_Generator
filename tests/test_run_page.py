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


class _FakeRepositoryService:
    def list_campaigns(self) -> list[str]:
        return ["flail", "kingmaker"]


def _services(fake_rc: _FakeRunController | None = None) -> AppServices:
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


def test_run_page_build_config_maps_form_fields() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["url_field"].value = "https://example.com/story"
    state["campaign_dropdown"].value = "flail"
    state["rerun_dropdown"].value = "script"
    state["recap_dropdown"].value = "short"
    state["skip_style_checkbox"].value = True
    state["panel_count_field"].value = "4"
    state["total_pages_field"].value = "2"
    state["model_field"].value = "gemini-3.2-flash"

    config = state["build_config"]()
    assert config.url == "https://example.com/story"
    assert config.campaign == "flail"
    assert config.rerun_from == "script"
    assert config.recap_version == "short"
    assert config.skip_style is True
    assert config.panel_count == 4
    assert config.total_pages == 2
    assert config.beater_model == "gemini-3.2-flash"
    assert config.script_model == "gemini-3.2-flash"


def test_run_page_build_config_full_run_maps_to_none_rerun() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["rerun_dropdown"].value = "full"
    config = state["build_config"]()
    assert config.rerun_from is None


def test_run_page_on_pipeline_event_updates_phase_badge() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    _container, state = build_run_page(_services(), page, event_log, ft)

    state["on_pipeline_event"](PhaseStarted(phase="script", message="Writing script..."))

    assert "script" in state["phase_badge"].value
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


@pytest.mark.asyncio
async def test_run_page_execute_run_calls_launch_with_correct_config() -> None:
    page = _FakePage()
    event_log = ft.ListView()
    fake_rc = _FakeRunController()
    _container, state = build_run_page(_services(fake_rc), page, event_log, ft)

    state["url_field"].value = "https://example.com/story"
    state["campaign_dropdown"].value = "flail"

    await state["execute_run"]()

    assert fake_rc.launched_config is not None
    assert fake_rc.launched_config.url == "https://example.com/story"
    assert fake_rc.launched_config.campaign == "flail"


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
