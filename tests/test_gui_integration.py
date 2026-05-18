from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("flet")

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from gui import (
    AppServices,
    append_pipeline_event,
    build_main_layout,
    close_settings_dialog,
    open_settings_dialog,
)
from pipeline_events import PhaseStarted
from repository_service import RepositoryService
from run_controller import RunController


class _FakeSession:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self._values[key] = value


class _FakePage:
    def __init__(self) -> None:
        self.title = ""
        self.theme_mode = None
        self.padding = 0
        self.dialog = None
        self.controls: list[object] = []
        self.update_calls = 0
        self.session = _FakeSession()

    def add(self, control: object) -> None:
        self.controls.append(control)

    def update(self) -> None:
        self.update_calls += 1


class _FakeSettingsService:
    def __init__(self) -> None:
        self._gemini_api_key = ""
        self._ollama_base_url = "http://localhost:11434/v1"
        self._default_model = "gemini-3.1-flash-lite"

    def get_gemini_api_key(self) -> str | None:
        return self._gemini_api_key

    def set_gemini_api_key(self, key: str) -> None:
        self._gemini_api_key = key

    def get_ollama_base_url(self) -> str:
        return self._ollama_base_url

    def set_ollama_base_url(self, url: str) -> None:
        self._ollama_base_url = url

    def get_default_model(self) -> str:
        return self._default_model

    def set_default_model(self, model: str) -> None:
        self._default_model = model

    def apply_to_environment(self) -> None:
        return


def _services(tmp_path: Path) -> AppServices:
    return AppServices(
        repository=RepositoryService(tmp_path / "campaigns"),
        settings=_FakeSettingsService(),
        run_controller=RunController(),
    )


def test_gui_main_layout_builds_tabs_and_event_log(tmp_path):
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path))

    navigation = controls["navigation"]
    assert [button.content for button in navigation.controls] == ["Run", "Prompts", "Output"]
    assert controls["run_view"].visible is True
    assert controls["prompt_view"].visible is False
    assert controls["output_view"].visible is False

    event_log = controls["event_log"]
    assert len(event_log.controls) == 1
    assert "GUI initialized" in event_log.controls[0].value


def test_gui_settings_dialog_opens_and_closes(tmp_path):
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path))
    dialog = controls["settings_dialog"]

    assert dialog.open is False
    open_settings_dialog(page, dialog)
    assert dialog.open is True
    assert page.dialog == dialog

    close_settings_dialog(page, dialog)
    assert dialog.open is False


def test_gui_event_log_receives_pipeline_events(tmp_path):
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path))
    event_log = controls["event_log"]

    append_pipeline_event(
        event_log,
        PhaseStarted(phase="script", message="Writing script..."),
        __import__("flet"),
    )

    assert len(event_log.controls) == 2
    assert "Writing script..." in event_log.controls[-1].value