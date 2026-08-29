from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("flet")

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from gui import (
    AppServices,
    append_pipeline_event,
    build_main_layout,
    build_output_page,
    build_prompt_page,
    close_settings_dialog,
    open_settings_dialog,
    _format_preview,
    _validate_art_template,
)
from pipeline_events import PhasePartialFailure, PhaseStarted, RunCompleted
from repository_service import RepositoryService
from run_controller import RunController, RunResult


class _FakeSession:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self._values[key] = value


class _FakeWindow:
    def __init__(self) -> None:
        self.width = None
        self.height = None
        self.min_width = None
        self.min_height = None


class _FakePage:
    def __init__(self) -> None:
        self.title = ""
        self.theme_mode = None
        self.padding = 0
        self.dialog = None
        self.controls: list[object] = []
        self.update_calls = 0
        self.session = _FakeSession()
        self.clipboard_text = ""
        self.clipboard_image = b""
        self.window = _FakeWindow()
        self._last_task_coro = None
        self._task_args: tuple[object, ...] = ()
        self._task_kwargs: dict[str, object] = {}

    def add(self, control: object) -> None:
        self.controls.append(control)

    def update(self) -> None:
        self.update_calls += 1

    def set_clipboard(self, value: str) -> None:
        self.clipboard_text = value

    def set_clipboard_image(self, value: bytes) -> None:
        self.clipboard_image = value

    def show_dialog(self, dialog: object) -> None:
        self.dialog = dialog
        setattr(dialog, "open", True)
        self.update_calls += 1

    def pop_dialog(self) -> None:
        if self.dialog is not None:
            setattr(self.dialog, "open", False)
        self.update_calls += 1

    def run_task(self, coro_func: object, *args: object, **kwargs: object) -> None:
        self._last_task_coro = coro_func
        self._task_args = args
        self._task_kwargs = kwargs


class _FakeSettingsService:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path
        self._gemini_api_key = ""
        self._ollama_base_url = "http://localhost:11434/v1"
        self._default_model = "gemini-3.1-flash-lite"
        self._image_generation_model = "gemini-2.5-flash-image"

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

    def get_image_generation_model(self) -> str:
        return self._image_generation_model

    def set_image_generation_model(self, model: str) -> None:
        self._image_generation_model = model

    def apply_to_environment(self) -> None:
        return


def _services(tmp_path: Path, settings: _FakeSettingsService | None = None) -> AppServices:
    return AppServices(
        repository=RepositoryService(tmp_path / "campaigns"),
        settings=settings or _FakeSettingsService(config_path=tmp_path / "settings.json"),  # type: ignore[arg-type]
        run_controller=RunController(),
    )


def _option_keys(dropdown: object) -> list[str]:
    return [option.key for option in dropdown.options]


def test_gui_main_layout_applies_window_size_via_page_window(tmp_path):
    page = _FakePage()

    build_main_layout(page, _services(tmp_path))

    assert page.window.width == 1500
    assert page.window.height == 1100
    assert page.window.min_width == 1200
    assert page.window.min_height == 900


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


def test_gui_settings_button_click_opens_dialog(tmp_path):
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path))
    dialog = controls["settings_dialog"]

    assert dialog.open is False
    controls["settings_button"].on_click(None)

    assert dialog.open is True
    assert page.dialog == dialog


def test_gui_settings_uses_dropdowns_for_default_and_image_models(tmp_path):
    import flet as ft

    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path))

    default_dropdown = controls["default_model_dropdown"]
    image_dropdown = controls["image_generation_model_dropdown"]

    assert isinstance(default_dropdown, ft.Dropdown)
    assert isinstance(image_dropdown, ft.Dropdown)
    assert default_dropdown.value == "gemini-3.1-flash-lite"
    assert image_dropdown.value == "gemini-2.5-flash-image"
    assert "gemini-3.1-flash-lite" in _option_keys(default_dropdown)
    assert "gemini-3.5-flash" in _option_keys(default_dropdown)
    assert "gemini-2.5-pro" in _option_keys(default_dropdown)
    assert "gemini-2.5-flash-image" in _option_keys(image_dropdown)
    assert "gemini-3.1-flash-image" in _option_keys(image_dropdown)
    assert "gemini-3.1-flash-lite-image" in _option_keys(image_dropdown)
    assert "gemini-3-pro-image" in _option_keys(image_dropdown)
    assert any("free" in option.text for option in default_dropdown.options)


@pytest.mark.asyncio
async def test_gui_fetches_gemini_models_on_startup_and_adds_them_to_dropdowns(tmp_path, monkeypatch):
    def fake_fetch(_api_key: str, **_kwargs):
        return (
            ["gemini-3.1-flash-lite", "gemini-3.1-pro"],
            ["gemini-2.5-flash-image", "gemini-3.1-flash-lite-image"],
        )

    monkeypatch.setattr("model_catalog.fetch_gemini_models", fake_fetch)

    settings = _FakeSettingsService(config_path=tmp_path / "settings.json")
    settings.set_gemini_api_key("secret-key")
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path, settings))

    assert page._last_task_coro is controls["fetch_models"]

    await controls["fetch_models"]()

    log_text = "\n".join(line.value for line in controls["event_log"].controls)
    assert "Fetching Gemini models" in log_text
    assert "gemini-3.1-pro" in _option_keys(controls["default_model_dropdown"])
    assert "gemini-3.1-flash-lite-image" in _option_keys(controls["image_generation_model_dropdown"])
    assert "gemini-3-pro-image" in _option_keys(controls["image_generation_model_dropdown"])
    assert "Loaded" in log_text
    assert "text" in log_text.lower()
    assert "image" in log_text.lower()


@pytest.mark.asyncio
async def test_gui_startup_status_warns_when_selected_models_are_deprecated(tmp_path, monkeypatch):
    def fake_fetch(_api_key: str, **_kwargs):
        return (["gemini-3.1-pro"], ["gemini-3.1-flash-image"])

    monkeypatch.setattr("model_catalog.fetch_gemini_models", fake_fetch)

    settings = _FakeSettingsService(config_path=tmp_path / "settings.json")
    settings.set_gemini_api_key("secret-key")
    settings.set_default_model("gemini-3.1-flash-lite")
    settings.set_image_generation_model("gemini-2.5-flash-image")
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path, settings))
    await controls["fetch_models"]()

    status = controls["status_text"].value
    warning = controls["settings_warning_text"].value
    log_text = "\n".join(line.value for line in controls["event_log"].controls)
    assert "gemini-3.1-flash-lite" in status
    assert "gemini-2.5-flash-image" in status
    assert "gemini-3.1-flash-lite" in warning
    assert "gemini-2.5-flash-image" in warning
    assert "gemini-3.1-flash-lite" in log_text
    assert "gemini-2.5-flash-image" in log_text
    assert any(
        option.key == "gemini-3.1-flash-lite" and "deprecated" in option.text
        for option in controls["default_model_dropdown"].options
    )
    assert any(
        option.key == "gemini-2.5-flash-image" and "deprecated" in option.text
        for option in controls["image_generation_model_dropdown"].options
    )


@pytest.mark.asyncio
async def test_gui_save_settings_keeps_deprecated_model_and_shows_validation(tmp_path, monkeypatch):
    def fake_fetch(_api_key: str, **_kwargs):
        return (["gemini-3.1-pro"], ["gemini-3.1-flash-image"])

    monkeypatch.setattr("model_catalog.fetch_gemini_models", fake_fetch)

    settings = _FakeSettingsService(config_path=tmp_path / "settings.json")
    settings.set_gemini_api_key("secret-key")
    settings.set_default_model("gemini-3.1-flash-lite")
    settings.set_image_generation_model("gemini-2.5-flash-image")
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path, settings))
    await controls["fetch_models"]()

    controls["settings_save_button"].on_click(None)

    assert settings.get_default_model() == "gemini-3.1-flash-lite"
    assert settings.get_image_generation_model() == "gemini-2.5-flash-image"
    assert "gemini-3.1-flash-lite" in controls["settings_warning_text"].value
    assert "gemini-2.5-flash-image" in controls["status_text"].value


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
    assert "[Run/script]" in event_log.controls[-1].value


def test_gui_event_log_failure_mentions_run_status(tmp_path):
    page = _FakePage()
    controls = build_main_layout(page, _services(tmp_path))
    event_log = controls["event_log"]

    append_pipeline_event(
        event_log,
        PhasePartialFailure(
            phase="script",
            message="Script generation failed",
            skipped_phases=["style", "prompt"],
            error_detail="KeyError: 'story_architecture' while normalizing story bible payload",
        ),
        __import__("flet"),
    )

    assert "See run_status.json for full details." in event_log.controls[-1].value
    assert "story_architecture" in event_log.controls[-1].value




@pytest.mark.asyncio
async def test_main_layout_run_completion_refreshes_output_file_list(tmp_path):
    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    v003 = episode_dir / "v003"
    v003.mkdir(parents=True, exist_ok=True)
    (v003 / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "episode-1",
                "url": "https://example.com/story",
                "title": "Episode 1",
                "created_at": "2026-05-19T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    class _WritingFakeRunController:
        """Emits RunCompleted, then writes run_status.json, then returns RunResult.

        This faithfully models the real sequence: event fires before persistence,
        but the file is present when RunResult is returned (and on_run_finished fires).
        """

        def launch_run(self, config: object, event_callback: object) -> "asyncio.Task[RunResult]":
            async def _complete() -> RunResult:
                import flet as _flet  # noqa: F401 – needed in the closure context
                event_callback(  # type: ignore[operator]
                    RunCompleted(
                        status="failed",
                        version="v003",
                        version_dir=str(v003),
                        checkpoints=[],
                        failed_phases=["script"],
                        error_messages=["script: error"],
                    )
                )
                # Persistence happens AFTER pipeline.run() returns
                (v003 / "run_status.json").write_text(
                    json.dumps({"status": "failed", "errors": ["script: error"]}),
                    encoding="utf-8",
                )
                return RunResult(
                    status="failed",
                    version="v003",
                    version_dir=str(v003),
                    failed_phases=["script"],
                    errors=["script: error"],
                    error_details=[],
                    events=[],
                    output=None,
                )

            return asyncio.ensure_future(_complete())

        def current_run(self) -> None:
            return None

        def last_result(self) -> None:
            return None

    page = _FakePage()
    services = AppServices(
        repository=RepositoryService(campaigns_root),
        settings=_FakeSettingsService(),  # type: ignore[arg-type]
        run_controller=_WritingFakeRunController(),  # type: ignore[arg-type]
    )
    controls = build_main_layout(page, services)

    await controls["run_page_state"]["execute_run"]()

    assert controls["output_page_state"]["campaign_dropdown"].value == "test_camp"
    assert controls["output_page_state"]["episode_dropdown"].value == "episode-1"
    assert controls["output_page_state"]["version_dropdown"].value == "v003"
    file_values = [radio.value for radio in controls["output_page_state"]["file_list"].content.controls]
    assert "run_status.json" in file_values


# ---------------------------------------------------------------------------
# Phase 5: Prompt workspace
# ---------------------------------------------------------------------------

def _make_campaign_prompts(tmp_path: Path, campaign: str = "test_camp") -> Path:
    """Create a minimal campaign directory with all 8 prompt files."""
    from prompter import ART_DIRECTION_TEMPLATE_FIELDS

    camp_dir = tmp_path / "campaigns" / campaign
    camp_dir.mkdir(parents=True)

    art = {name: f"value for {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    art_dir = camp_dir / "art_direction"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "brutalist.json").write_text(json.dumps(art), encoding="utf-8")
    for filename in (
        "story_architect_system.txt",
        "story_architect_user.txt",
        "scriptwriter_system.txt",
        "scriptwriter_user.txt",
        "style_integrator_system.txt",
        "style_integrator_user.txt",
        "page_prompt.txt",
    ):
        (camp_dir / filename).write_text(f"default {filename}", encoding="utf-8")

    return tmp_path / "campaigns"


def _mark_campaign_as_has_run(campaigns_root: Path, campaign: str = "test_camp") -> None:
    episode_dir = campaigns_root / campaign / "episode-1"
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "episode-1",
                "url": "https://example.com/story",
                "title": "Episode 1",
                "created_at": "2026-05-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (episode_dir / "v001").mkdir(parents=True, exist_ok=True)


def _make_output_versions(tmp_path: Path, campaign: str = "test_camp") -> Path:
    campaigns_root = tmp_path / "campaigns"
    camp_dir = campaigns_root / campaign
    episode_dir = camp_dir / "episode-1"
    v001 = episode_dir / "v001"
    v002 = episode_dir / "v002"

    v001.mkdir(parents=True, exist_ok=True)
    v002.mkdir(parents=True, exist_ok=True)

    (episode_dir / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "episode-1",
                "url": "https://example.com/story",
                "title": "Episode 1",
                "created_at": "2026-05-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    (v001 / "01_raw_text.json").write_text('{"a":1}', encoding="utf-8")
    (v001 / "03_script.json").write_text('{"script":"old"}', encoding="utf-8")
    (v001 / "04_page_1_prompt.txt").write_text("old prompt", encoding="utf-8")
    (v001 / "run_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "checkpoints": ["scrape", "entities"],
                "failed": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    (v002 / "01_raw_text.json").write_text('{"a":2,"b":[1,2]}', encoding="utf-8")
    (v002 / "03_script.json").write_text('{"script":"new"}', encoding="utf-8")
    (v002 / "04_page_1_prompt.txt").write_text("new prompt", encoding="utf-8")
    (v002 / "run_status.json").write_text(
        json.dumps(
            {
                "status": "partial",
                "checkpoints": ["scrape", "entities", "script"],
                "failed": ["style"],
                "errors": ["style timeout"],
                "warnings": ["fallback used"],
            }
        ),
        encoding="utf-8",
    )

    return campaigns_root


def _prompt_services(campaigns_root: Path) -> AppServices:
    return AppServices(
        repository=RepositoryService(campaigns_root),
        settings=_FakeSettingsService(),
        run_controller=RunController(),
    )


def test_prompt_page_builds_with_campaign_files(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    assert state["campaign_dropdown"].value == "test_camp"
    labels = [c.label for c in state["file_list"].content.controls]
    # Bundled + campaign art styles, then the 7 non-art prompt templates.
    assert any("brutalist (bundled)" in label for label in labels)
    assert any("brutalist (campaign)" in label for label in labels)
    assert any(label.startswith("page_prompt.txt") for label in labels)
    assert len(state["file_list"].content.controls) >= 8


def test_prompt_page_load_reads_file_into_editor(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    # Simulate selecting scriptwriter_system
    state["selected_key"][0] = "scriptwriter_system"
    state["paths"]["scriptwriter_system"] = (
        campaigns_root / "test_camp" / "scriptwriter_system.txt"
    )
    state["on_load"](None)

    assert state["editor"].value == "default scriptwriter_system.txt"


def test_prompt_page_radio_on_change_uses_control_value(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    event = type(
        "RadioChangeEvent",
        (),
        {"control": type("RadioControl", (), {"value": "scriptwriter_system"})()},
    )()

    state["file_list"].on_change(event)

    assert state["selected_key"][0] == "scriptwriter_system"
    assert state["editor"].value == "default scriptwriter_system.txt"


def test_prompt_page_default_source_is_campaign_before_any_run(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    campaign_file = campaigns_root / "test_camp" / "scriptwriter_system.txt"
    campaign_file.write_text("campaign override", encoding="utf-8")

    event = type(
        "RadioChangeEvent",
        (),
        {"control": type("RadioControl", (), {"value": "scriptwriter_system"})()},
    )()
    state["file_list"].on_change(event)

    assert state["editor"].value == "campaign override"


def test_prompt_page_campaign_switch_reloads_editor_content(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    second = campaigns_root / "other_camp"
    second.mkdir(parents=True, exist_ok=True)
    for filename in (
        "story_architect_system.txt",
        "story_architect_user.txt",
        "scriptwriter_system.txt",
        "scriptwriter_user.txt",
        "style_integrator_system.txt",
        "style_integrator_user.txt",
        "page_prompt.txt",
    ):
        src = campaigns_root / "test_camp" / filename
        dst = second / filename
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    art_src = campaigns_root / "test_camp" / "art_direction" / "brutalist.json"
    art_dst = second / "art_direction" / "brutalist.json"
    art_dst.parent.mkdir(parents=True, exist_ok=True)
    art_dst.write_text(art_src.read_text(encoding="utf-8"), encoding="utf-8")
    (campaigns_root / "test_camp" / "scriptwriter_system.txt").write_text(
        "script A", encoding="utf-8"
    )
    (second / "scriptwriter_system.txt").write_text("script B", encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    state["campaign_dropdown"].on_select(
        type(
            "CampaignChangeEvent",
            (),
            {"control": type("CampaignControl", (), {"value": "other_camp"})()},
        )()
    )
    state["file_list"].on_change(
        type(
            "RadioChangeEvent",
            (),
            {"control": type("RadioControl", (), {"value": "scriptwriter_system"})()},
        )()
    )

    assert state["editor"].value == "script B"


def test_prompt_page_campaign_switch_uses_event_data(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    second = campaigns_root / "other_camp"
    second.mkdir(parents=True, exist_ok=True)
    for filename in (
        "story_architect_system.txt",
        "story_architect_user.txt",
        "scriptwriter_system.txt",
        "scriptwriter_user.txt",
        "style_integrator_system.txt",
        "style_integrator_user.txt",
        "page_prompt.txt",
    ):
        src = campaigns_root / "test_camp" / filename
        dst = second / filename
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    art_src = campaigns_root / "test_camp" / "art_direction" / "brutalist.json"
    art_dst = second / "art_direction" / "brutalist.json"
    art_dst.parent.mkdir(parents=True, exist_ok=True)
    art_dst.write_text(art_src.read_text(encoding="utf-8"), encoding="utf-8")
    (second / "scriptwriter_system.txt").write_text("from event data", encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    state["campaign_dropdown"].on_select(
        type(
            "CampaignChangeEvent",
            (),
            {
                "data": "other_camp",
                "control": type("CampaignControl", (), {"value": None})(),
            },
        )()
    )
    state["file_list"].on_change(
        type(
            "RadioChangeEvent",
            (),
            {
                "data": "scriptwriter_system",
                "control": type("RadioControl", (), {"value": None})(),
            },
        )()
    )

    assert state["campaign_dropdown"].value == "other_camp"
    assert state["editor"].value == "from event data"


def test_prompt_page_default_source_is_campaign_after_any_run(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    _mark_campaign_as_has_run(campaigns_root)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    campaign_file = campaigns_root / "test_camp" / "scriptwriter_system.txt"
    campaign_file.write_text("campaign after run", encoding="utf-8")

    event = type(
        "RadioChangeEvent",
        (),
        {"control": type("RadioControl", (), {"value": "scriptwriter_system"})()},
    )()
    state["file_list"].on_change(event)

    assert state["editor"].value == "campaign after run"


def test_prompt_page_uses_src_prompts_when_no_campaign_selected(tmp_path):
    import flet as ft
    from prompt_templates import DEFAULT_PROMPTS_DIR

    campaigns_root = tmp_path / "campaigns"
    campaigns_root.mkdir(parents=True, exist_ok=True)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    assert state["campaign_dropdown"].value is None
    labels = [c.label for c in state["file_list"].content.controls]
    assert any("brutalist (bundled)" in label for label in labels)
    assert not any("(campaign)" in label for label in labels)
    from prompter import DEFAULT_ART_DIRECTION_TEMPLATE_PATH

    # First radio is first bundled style alphabetically, not necessarily brutalist.
    first_key = state["file_list"].content.controls[0].value
    assert first_key.startswith("bundled:")
    assert state["editor"].value  # loaded something from bundled library
    assert "art_direction" in state["source_dir_text"].value or str(
        DEFAULT_ART_DIRECTION_TEMPLATE_PATH.parent
    ) in state["source_dir_text"].value


def test_prompt_page_shows_campaign_source_dir_for_campaign_file(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    event = type(
        "RadioChangeEvent",
        (),
        {"control": type("RadioControl", (), {"value": "scriptwriter_system"})()},
    )()
    state["file_list"].on_change(event)

    expected_dir = campaigns_root / "test_camp"
    assert str(expected_dir) in state["source_dir_text"].value


def test_prompt_page_save_writes_file(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    target = campaigns_root / "test_camp" / "scriptwriter_user.txt"
    state["selected_key"][0] = "scriptwriter_user"
    state["paths"]["scriptwriter_user"] = target
    state["editor"].value = "updated content"
    state["on_save"](None)

    assert target.read_text(encoding="utf-8") == "updated content"


def test_prompt_page_save_always_writes_campaign_file(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    target = campaigns_root / "test_camp" / "page_prompt.txt"
    state["selected_key"][0] = "page_prompt"
    state["paths"]["page_prompt"] = target
    state["editor"].value = "campaign saved prompt"
    state["on_save"](None)

    assert target.read_text(encoding="utf-8") == "campaign saved prompt"


def test_prompt_page_save_rejects_invalid_art_template(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    target = campaigns_root / "test_camp" / "art_direction" / "brutalist.json"
    original = target.read_text(encoding="utf-8")
    state["selected_key"][0] = "campaign:brutalist"
    state["paths"]["campaign:brutalist"] = target
    state["editor"].value = "{}"  # empty object is invalid
    state["on_save"](None)

    assert state["validation_text"].value != ""
    # File must NOT have been modified
    assert target.read_text(encoding="utf-8") == original


def test_prompt_page_save_valid_art_template(tmp_path):
    import flet as ft
    from prompter import ART_DIRECTION_TEMPLATE_FIELDS

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    target = campaigns_root / "test_camp" / "art_direction" / "brutalist.json"
    valid_art = {name: f"updated {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    state["selected_key"][0] = "campaign:brutalist"
    state["paths"]["campaign:brutalist"] = target
    state["editor"].value = json.dumps(valid_art)
    state["on_save"](None)

    assert "campaign override" in state["validation_text"].value
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["base_style"] == "updated base_style"
    assert state["selected_key"][0] == "campaign:brutalist"


def test_prompt_page_save_bundled_art_writes_campaign_override(tmp_path):
    import flet as ft
    from prompter import ART_DIRECTION_TEMPLATE_FIELDS

    campaigns_root = tmp_path / "campaigns"
    camp = campaigns_root / "fresh_camp"
    camp.mkdir(parents=True)
    # Non-art campaign prompts only — no campaign art_direction yet.
    for filename in (
        "story_architect_system.txt",
        "story_architect_user.txt",
        "scriptwriter_system.txt",
        "scriptwriter_user.txt",
        "style_integrator_system.txt",
        "style_integrator_user.txt",
        "page_prompt.txt",
    ):
        (camp / filename).write_text(f"default {filename}", encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    valid_art = {name: f"override {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    state["selected_key"][0] = "bundled:brutalist"
    state["editor"].value = json.dumps(valid_art)
    state["on_save"](None)

    target = camp / "art_direction" / "brutalist.json"
    assert target.exists()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["base_style"] == "override base_style"
    assert state["selected_key"][0] == "campaign:brutalist"
    labels = [c.label for c in state["file_list"].content.controls]
    assert any("brutalist (campaign)" in label for label in labels)


def test_prompt_page_reset_restores_default(tmp_path):
    import flet as ft
    from prompt_templates import DEFAULT_PROMPTS_DIR

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    default_content = (DEFAULT_PROMPTS_DIR / "page_prompt.txt").read_text(encoding="utf-8")

    state["selected_key"][0] = "page_prompt"
    state["editor"].value = "something random"
    state["on_reset"](None)

    assert state["editor"].value == default_content


def test_prompt_page_file_list_scrolls(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    assert state["file_list"].content.scroll == ft.ScrollMode.AUTO


def test_prompt_page_copy_editor_to_clipboard(tmp_path):
    import flet as ft

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    state["editor"].value = "prompt body to copy"
    assert "on_copy_content" in state
    asyncio.run(state["on_copy_content"](None))

    assert page.clipboard_text == "prompt body to copy"


def test_set_clipboard_uses_flet_clipboard_service_when_page_has_no_setter():
    """Modern Flet pages lack set_clipboard; use Clipboard().set instead."""
    import flet as ft

    from gui import _set_clipboard

    class _PageWithoutSetter:
        pass

    class _FakeClipboard:
        last_value: str | None = None

        def set(self, value: str):
            async def _set() -> None:
                _FakeClipboard.last_value = value

            return _set()

    class _FakeFt:
        Clipboard = _FakeClipboard

    asyncio.run(_set_clipboard(_PageWithoutSetter(), "via service", _FakeFt()))
    assert _FakeClipboard.last_value == "via service"
    # Sanity: real flet still exposes Clipboard.
    assert hasattr(ft, "Clipboard")


def test_set_clipboard_image_uses_page_setter_when_present():
    from gui import _set_clipboard_image

    page = _FakePage()
    asyncio.run(_set_clipboard_image(page, b"png-bytes", None))
    assert page.clipboard_image == b"png-bytes"


def test_set_clipboard_image_uses_flet_clipboard_service_when_page_has_no_setter():
    import flet as ft

    from gui import _set_clipboard_image

    class _PageWithoutSetter:
        pass

    class _FakeClipboard:
        last_image: bytes | None = None

        def set_image(self, value: bytes):
            async def _set() -> None:
                _FakeClipboard.last_image = value

            return _set()

    class _FakeFt:
        Clipboard = _FakeClipboard

    asyncio.run(_set_clipboard_image(_PageWithoutSetter(), b"png-bytes", _FakeFt()))
    assert _FakeClipboard.last_image == b"png-bytes"
    assert hasattr(ft.Clipboard, "set_image")


def test_macos_png_clipboard_script_reads_file_as_png():
    from gui import _macos_png_clipboard_script

    script = _macos_png_clipboard_script("/tmp/comic page.png")
    assert script.count("(") == script.count(")")
    assert script == (
        'set the clipboard to (read (POSIX file "/tmp/comic page.png") as «class PNGf»)'
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS osascript clipboard")
def test_copy_image_to_os_clipboard_succeeds_with_real_png():
    import io
    import subprocess

    from PIL import Image

    from gui import _copy_image_to_os_clipboard

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG")
    _copy_image_to_os_clipboard(buffer.getvalue())

    info = subprocess.run(
        ["osascript", "-e", "clipboard info"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "PNGf" in info.stdout


def test_format_preview_preserves_unicode_in_json(tmp_path):
    """JSON previews should show real punctuation, not \\uXXXX escapes."""
    path = tmp_path / "02_5_story_bible.json"
    path.write_text(
        json.dumps(
            {"summary": "looms—a Syndicate’s plan"},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    preview = _format_preview(path)

    assert "—" in preview
    assert "’" in preview
    assert r"\u2014" not in preview
    assert r"\u2019" not in preview


def test_prompt_page_art_styles_sorted_with_campaign_interleaved(tmp_path, monkeypatch):
    import flet as ft
    from art_styles import campaign_art_direction_dir

    campaigns_root = _make_campaign_prompts(tmp_path)
    bundled = tmp_path / "bundled_styles"
    bundled.mkdir()
    for stem in ("brutalist", "zzz"):
        (bundled / f"{stem}.json").write_text(
            json.dumps(
                {
                    "base_style": stem,
                    "characters": "c",
                    "color_palette": "p",
                    "layout_and_composition": "l",
                    "lettering_and_dialog": "d",
                    "text_rendering_guide": "t",
                }
            ),
            encoding="utf-8",
        )
    camp_dir = campaign_art_direction_dir(campaigns_root, "test_camp")
    camp_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("aaa", "brutalist"):
        (camp_dir / f"{stem}.json").write_text(
            json.dumps(
                {
                    "base_style": stem,
                    "characters": "c",
                    "color_palette": "p",
                    "layout_and_composition": "l",
                    "lettering_and_dialog": "d",
                    "text_rendering_guide": "t",
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr("art_styles.bundled_art_direction_dir", lambda: bundled)
    monkeypatch.setattr("gui.default_art_direction_template_path", lambda: bundled / "brutalist.json")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    art_labels = [
        c.label
        for c in state["file_list"].content.controls
        if "(bundled)" in c.label or "(campaign)" in c.label
    ]
    assert art_labels == [
        "aaa (campaign)",
        "brutalist (bundled)",
        "brutalist (campaign)",
        "zzz (bundled)",
    ]


def test_validate_art_template_passes_valid():
    from prompter import ART_DIRECTION_TEMPLATE_FIELDS

    art = {name: f"val {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    assert _validate_art_template(json.dumps(art)) is None


def test_validate_art_template_fails_bad_json():
    err = _validate_art_template("{not json}")
    assert err is not None and "JSON" in err


def test_validate_art_template_fails_empty_object():
    err = _validate_art_template("{}")
    assert err is not None and "at least one" in err.lower()


def test_validate_art_template_accepts_custom_keys_only():
    err = _validate_art_template('{"line_weight": "Heavy ink only."}')
    assert err is None


def test_validate_art_template_accepts_extra_keys():
    from prompter import ART_DIRECTION_TEMPLATE_FIELDS

    art = {name: f"val {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    art["shadow_behavior"] = "Shadows stretch across panels."
    assert _validate_art_template(json.dumps(art)) is None


# ---------------------------------------------------------------------------
# Phase 6: Output workspace
# ---------------------------------------------------------------------------

def test_output_page_latest_version_preselected(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["campaign_dropdown"].value == "test_camp"
    assert state["episode_dropdown"].value == "episode-1"
    assert state["version_dropdown"].value == "v002"


def test_output_page_lists_files_for_selected_version(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    labels = [radio.label for radio in state["file_list"].content.controls]
    assert "01_raw_text.json" in labels
    assert "03_script.json" in labels
    assert "04_page_1_prompt.txt" in labels


def test_output_page_copy_preview_to_clipboard(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["preview"].value = "preview body to copy"
    assert "on_copy_content" in state
    asyncio.run(state["on_copy_content"](None))

    assert page.clipboard_text == "preview body to copy"


def test_output_page_json_preview_is_pretty(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    event = type(
        "RadioChangeEvent",
        (),
        {"control": type("RadioControl", (), {"value": "01_raw_text.json"})()},
    )()
    state["file_list"].value = "01_raw_text.json"
    state["file_list"].on_change(event)

    assert "\n" in state["preview"].value
    assert "  \"a\"" in state["preview"].value


def test_output_page_build_rerun_config_uses_live_controls(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    (episode_dir / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "episode-1",
                "url": "https://example.com/story",
                "title": "Episode 1",
                "created_at": "2026-05-18T00:00:00Z",
                "panel_count": 6,
                "total_pages": 1,
                "recap_version": "standard",
                "aspect_ratio": "3:2",
            }
        ),
        encoding="utf-8",
    )

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["panel_count_field"].value = "10"
    state["total_pages_field"].value = "4"
    state["recap_dropdown"].value = "long"
    state["aspect_ratio_dropdown"].value = "4:3"
    state["vignette_checkbox"].value = True

    config = state["build_rerun_config"]("test_camp", "episode-1", "architect")

    assert config.panel_count == 10
    assert config.total_pages == 4
    assert config.recap_version == "long"
    assert config.aspect_ratio == "4:3"
    assert config.vignette is True
    assert config.stop_after is None


def test_output_page_rerun_only_stage_toggle_defaults_off_and_sets_stop_after(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    (episode_dir / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "episode-1",
                "url": "https://example.com/story",
                "title": "Episode 1",
                "created_at": "2026-05-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["rerun_only_stage_checkbox"].value is False

    config_off = state["build_rerun_config"]("test_camp", "episode-1", "entities")
    assert config_off.rerun_from == "entities"
    assert config_off.stop_after is None

    state["rerun_only_stage_checkbox"].value = True
    config_on = state["build_rerun_config"]("test_camp", "episode-1", "entities")
    assert config_on.rerun_from == "entities"
    assert config_on.stop_after == "entities"


def test_output_page_shows_version_settings_from_run_status(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    (version_dir / "run_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "run_config": {
                    "panel_count": 8,
                    "total_pages": 3,
                    "recap_version": "short",
                    "aspect_ratio": "3:2",
                    "generation_mode": "panel",
                    "vignette": True,
                },
            }
        ),
        encoding="utf-8",
    )

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert "Panels: 8" in state["settings_text"].value
    assert "Pages: 3" in state["settings_text"].value
    assert "Recap: short" in state["settings_text"].value
    assert "Aspect ratio: 3:2" in state["settings_text"].value
    assert "Generation: Panel by Panel" in state["settings_text"].value
    assert "Vignette: on" in state["settings_text"].value
    assert state["generation_mode_dropdown"].value == "panel"
    assert state["vignette_checkbox"].value is True


def test_output_page_version_change_updates_loaded_and_settings_text(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    (episode_dir / "v001" / "run_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "run_config": {
                    "panel_count": 4,
                    "total_pages": 2,
                    "recap_version": "short",
                    "aspect_ratio": "1:1",
                    "generation_mode": "panel",
                    "vignette": True,
                    "art_style": "bundled:bruise_and_bile_grok_3",
                },
            }
        ),
        encoding="utf-8",
    )
    (episode_dir / "v002" / "run_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "run_config": {
                    "panel_count": 6,
                    "total_pages": 1,
                    "recap_version": "standard",
                    "aspect_ratio": "3:2",
                    "generation_mode": "page",
                    "vignette": False,
                    "art_style": "bundled:dark-fantasy",
                },
            }
        ),
        encoding="utf-8",
    )

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["version_dropdown"].value == "v002"
    assert state["output_status_text"].value == "Loaded: test_camp / episode-1 / v002"
    assert "Panels: 6" in state["settings_text"].value
    assert "Pages: 1" in state["settings_text"].value
    assert "Recap: standard" in state["settings_text"].value
    assert "Aspect ratio: 3:2" in state["settings_text"].value
    assert "Generation: Page by Page" in state["settings_text"].value
    assert "Vignette: off" in state["settings_text"].value
    assert "Art style: bundled:dark-fantasy" in state["settings_text"].value

    _select_output_version(state, "v001")

    assert state["version_dropdown"].value == "v001"
    assert state["output_status_text"].value == "Loaded: test_camp / episode-1 / v001"
    assert "Panels: 4" in state["settings_text"].value
    assert "Pages: 2" in state["settings_text"].value
    assert "Recap: short" in state["settings_text"].value
    assert "Aspect ratio: 1:1" in state["settings_text"].value
    assert "Generation: Panel by Panel" in state["settings_text"].value
    assert "Vignette: on" in state["settings_text"].value
    assert "Art style: bundled:bruise_and_bile_grok_3" in state["settings_text"].value
    assert state["generation_mode_dropdown"].value == "panel"
    assert state["vignette_checkbox"].value is True


def test_output_page_run_status_shows_errors_and_warnings(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    status_text = state["run_status_text"].value
    assert "status=partial" in status_text
    assert "failed=[style]" in status_text
    assert "errors=[style timeout]" in status_text
    assert "warnings=[fallback used]" in status_text


def test_output_page_aspect_ratio_dropdown_uses_orientation_names(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    dropdown = state["aspect_ratio_dropdown"]
    labels = {o.key: o.text for o in dropdown.options}
    assert labels["1:1"] == "1:1 — Square"
    assert labels["4:3"] == "4:3 — Vertical"
    assert labels["3:2"] == "3:2 — Horizontal"


def test_output_page_exposes_generation_controls(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["generate_images_button"] is not None
    assert state["generate_selected_image_button"] is not None
    assert state["test_image_button"] is not None
    assert state["stitch_images_button"] is not None


def test_output_page_action_buttons_disable_when_started(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)

    class _BusyPage:
        def __init__(self) -> None:
            self.update_calls = 0
            self.last_task = None

        def update(self) -> None:
            self.update_calls += 1

        def run_task(self, task: object) -> None:
            self.last_task = task

    page = _BusyPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["quick_rerun_button"].on_click(None)
    assert state["quick_rerun_button"].disabled is True

    state["generate_images_button"].on_click(None)
    assert state["generate_images_button"].disabled is True

    state["test_image_button"].on_click(None)
    assert state["test_image_button"].disabled is True

    state["generate_selected_image_button"].visible = True
    state["generate_selected_image_button"].on_click(None)
    assert state["generate_selected_image_button"].disabled is True


def test_output_page_regenerating_panel_prompt_uses_panel_output_path(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    (version_dir / "04_page_1_panel_1_prompt.txt").write_text("panel prompt", encoding="utf-8")

    class _TaskPage(_FakePage):
        def run_task(self, task: object) -> None:
            asyncio.run(task())

    fake_generator = type("FakeGenerator", (), {"generate_image": lambda self, prompt: b"panel-bytes", "save_image": None})()
    fake_generator.save_image = lambda image_bytes, output_path: Path(output_path).write_bytes(image_bytes) or Path(output_path)

    monkeypatch.setattr("gui.ImageGenerator", lambda model: fake_generator)
    stitched = version_dir / "06_page_1.png"
    monkeypatch.setattr("gui.stitch_panel_images", lambda *args, **kwargs: stitched)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["file_list"].value = "04_page_1_panel_1_prompt.txt"
    state["file_list"].on_change(type("Event", (), {"control": type("Control", (), {"value": "04_page_1_panel_1_prompt.txt"})()})())

    state["generate_selected_image_button"].on_click(None)

    assert (version_dir / "images" / "v001" / "05_page_1_panel_1.png").exists()
    assert stitched.exists() is False


def test_output_page_generate_images_writes_into_selected_version(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    version_dir = episode_dir / "v002"
    (version_dir / "04_page_2_prompt.txt").write_text("page two", encoding="utf-8")

    class _TaskPage(_FakePage):
        def run_task(self, task: object) -> None:
            asyncio.run(task())

    fake_generator = type(
        "FakeGenerator",
        (),
        {
            "generate_image": lambda self, prompt: f"img:{prompt}".encode(),
            "save_image": lambda self, image_bytes, output_path: (
                Path(output_path).parent.mkdir(parents=True, exist_ok=True),
                Path(output_path).write_bytes(image_bytes),
                Path(output_path),
            )[-1],
        },
    )()
    monkeypatch.setattr("gui.ImageGenerator", lambda model: fake_generator)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["generate_images_button"].on_click(None)

    assert (version_dir / "images" / "v001" / "05_page_1.png").exists()
    assert (version_dir / "images" / "v001" / "05_page_2.png").exists()
    status = json.loads((version_dir / "run_status.json").read_text(encoding="utf-8"))
    assert status["run_config"]["image_generation_model"] == "gemini-2.5-flash-image"
    assert status["image_generations"] == [
        {
            "model": "gemini-2.5-flash-image",
            "images_dir": "images/v001",
            "files": ["05_page_1.png", "05_page_2.png"],
            "source": "generate_all",
            "errors": [],
            "character_ref_slugs": [],
        }
    ]
    assert (episode_dir / "v002").is_dir()
    labels = [control.label for control in state["file_list"].content.controls]
    assert "images/v001/05_page_1.png" in labels
    version_names = {path.name for path in episode_dir.iterdir() if path.is_dir()}
    assert version_names == {"v001", "v002"}


def test_output_page_generate_images_attaches_campaign_character_refs(tmp_path, monkeypatch):
    import flet as ft

    from entities import Character, WorldStateCheckpoint
    from scriptwriter import Page, Panel, ScriptCheckpoint

    campaigns_root = _make_output_versions(tmp_path)
    campaign_root = campaigns_root / "test_camp"
    version_dir = campaign_root / "episode-1" / "v002"
    characters_dir = campaign_root / "characters"
    characters_dir.mkdir()
    (characters_dir / "Del.png").write_bytes(b"del-portrait")
    world = WorldStateCheckpoint(
        url="https://example.com/story",
        model="test",
        player_characters=[Character(name="Del", description="A druid")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-01-01T00:00:00+00:00",
    )
    script = ScriptCheckpoint(
        url="https://example.com/story",
        model="test",
        panel_count=1,
        total_pages=1,
        pages=[
            Page(
                page_number=1,
                panel_count=1,
                panels=[
                    Panel(
                        index=1,
                        page_number=1,
                        panel_scale="medium",
                        panel_shape="standard",
                        setting="A marsh",
                        visual_action="Del raises a torch.",
                        characters=["Del"],
                    )
                ],
            )
        ],
        scripted_at="2026-01-01T00:00:00+00:00",
    )
    (version_dir / "02_5_episode_entities.json").write_text(
        json.dumps(world.model_dump(mode="json")),
        encoding="utf-8",
    )
    (version_dir / "03_script_page_001.json").write_text(
        json.dumps(script.model_dump(mode="json")),
        encoding="utf-8",
    )

    class _TaskPage(_FakePage):
        def run_task(self, task: object) -> None:
            asyncio.run(task())

    class _CapturingGenerator:
        def __init__(self) -> None:
            self.reference_images: list[object] = []

        def generate_image(self, prompt: str, reference_images=None) -> bytes:
            self.reference_images.append(reference_images)
            return f"img:{prompt}".encode()

        def save_image(self, image_bytes: bytes, output_path: Path) -> Path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_bytes)
            return output_path

    fake_generator = _CapturingGenerator()
    monkeypatch.setattr("gui.ImageGenerator", lambda model: fake_generator)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["generate_images_button"].on_click(None)

    assert fake_generator.reference_images
    first_refs = fake_generator.reference_images[0]
    assert first_refs is not None
    assert [ref.name for ref in first_refs] == ["Del"]
    assert first_refs[0].data == b"del-portrait"
    status = json.loads((version_dir / "run_status.json").read_text(encoding="utf-8"))
    assert status["image_generations"][0]["character_ref_slugs"] == ["Del"]


def test_output_page_generate_images_shows_and_records_errors(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    error_message = "model gemini-3.1-flash-lite-image is not found"

    class _TaskPage(_FakePage):
        def run_task(self, task: object) -> None:
            asyncio.run(task())

    class _FailingGenerator:
        def generate_image(self, prompt: str) -> bytes:
            raise RuntimeError(error_message)

        def save_image(self, image_bytes: bytes, output_path: Path) -> Path:
            return Path(output_path)

    monkeypatch.setattr("gui.ImageGenerator", lambda model: _FailingGenerator())

    page = _TaskPage()
    event_log = ft.ListView()
    services = _prompt_services(campaigns_root)
    services.settings.set_image_generation_model("gemini-3.1-flash-lite-image")
    _view, state = build_output_page(services, page, ft, event_log)

    state["generate_images_button"].on_click(None)

    status_text = state["output_status_text"].value
    assert "Generated 0 image(s) in images/v001" in status_text
    assert error_message in status_text
    assert "1 error(s)" in status_text

    status = json.loads((version_dir / "run_status.json").read_text(encoding="utf-8"))
    assert status["image_generations"] == [
        {
            "model": "gemini-3.1-flash-lite-image",
            "images_dir": "images/v001",
            "files": [],
            "source": "generate_all",
            "errors": [f"image_generation: page 1: {error_message}"],
            "character_ref_slugs": [],
        }
    ]
    log_text = "\n".join(line.value for line in event_log.controls)
    assert error_message in log_text
    assert "Images" in log_text


def test_output_page_test_image_generates_selected_prompt(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    (version_dir / "04_page_2_prompt.txt").write_text("page two", encoding="utf-8")

    class _TaskPage(_FakePage):
        def run_task(self, task: object) -> None:
            asyncio.run(task())

    fake_generator = type(
        "FakeGenerator",
        (),
        {
            "generate_image": lambda self, prompt: f"img:{prompt}".encode(),
            "save_image": lambda self, image_bytes, output_path: (
                Path(output_path).parent.mkdir(parents=True, exist_ok=True),
                Path(output_path).write_bytes(image_bytes),
                Path(output_path),
            )[-1],
        },
    )()
    monkeypatch.setattr("gui.ImageGenerator", lambda model: fake_generator)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["file_list"].value = "04_page_2_prompt.txt"
    state["file_list"].on_change(
        type("Event", (), {"control": type("Control", (), {"value": "04_page_2_prompt.txt"})()})()
    )
    state["test_image_button"].on_click(None)

    image_path = version_dir / "images" / "v001" / "05_page_2.png"
    assert image_path.exists()
    assert image_path.read_bytes() == b"img:page two"


def test_output_page_test_image_after_generate_all_adds_suffix(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"

    class _TaskPage(_FakePage):
        def run_task(self, task: object) -> None:
            asyncio.run(task())

    fake_generator = type(
        "FakeGenerator",
        (),
        {
            "generate_image": lambda self, prompt: f"img:{prompt}".encode(),
            "save_image": lambda self, image_bytes, output_path: (
                Path(output_path).parent.mkdir(parents=True, exist_ok=True),
                Path(output_path).write_bytes(image_bytes),
                Path(output_path),
            )[-1],
        },
    )()
    monkeypatch.setattr("gui.ImageGenerator", lambda model: fake_generator)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["generate_images_button"].on_click(None)
    state["file_list"].value = "04_page_1_prompt.txt"
    state["file_list"].on_change(
        type("Event", (), {"control": type("Control", (), {"value": "04_page_1_prompt.txt"})()})()
    )
    state["test_image_button"].on_click(None)

    images_dir = version_dir / "images" / "v001"
    assert (images_dir / "05_page_1.png").exists()
    assert (images_dir / "05_page_1_v1.png").exists()
    assert sorted(path.name for path in (version_dir / "images").iterdir()) == ["v001"]
    status = json.loads((version_dir / "run_status.json").read_text(encoding="utf-8"))
    assert [entry["model"] for entry in status["image_generations"]] == [
        "gemini-2.5-flash-image",
        "gemini-2.5-flash-image",
    ]
    assert [entry["source"] for entry in status["image_generations"]] == [
        "generate_all",
        "test_image",
    ]
    assert status["image_generations"][1]["files"] == ["05_page_1_v1.png"]


def test_output_page_test_image_records_a_second_model(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"

    class _TaskPage(_FakePage):
        def run_task(self, task: object) -> None:
            asyncio.run(task())

    fake_generator = type(
        "FakeGenerator",
        (),
        {
            "generate_image": lambda self, prompt: f"img:{prompt}".encode(),
            "save_image": lambda self, image_bytes, output_path: (
                Path(output_path).parent.mkdir(parents=True, exist_ok=True),
                Path(output_path).write_bytes(image_bytes),
                Path(output_path),
            )[-1],
        },
    )()
    monkeypatch.setattr("gui.ImageGenerator", lambda model: fake_generator)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["generate_images_button"].on_click(None)
    services.settings.set_image_generation_model("gemini-3.1-flash-image")
    state["file_list"].value = "04_page_1_prompt.txt"
    state["file_list"].on_change(
        type("Event", (), {"control": type("Control", (), {"value": "04_page_1_prompt.txt"})()})()
    )
    state["test_image_button"].on_click(None)

    status = json.loads((version_dir / "run_status.json").read_text(encoding="utf-8"))
    assert [entry["model"] for entry in status["image_generations"]] == [
        "gemini-2.5-flash-image",
        "gemini-3.1-flash-image",
    ]
    assert status["run_config"]["image_generation_model"] == "gemini-3.1-flash-image"


def test_output_page_prompt_selection_enables_single_image_generation(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["file_list"].value = "04_page_1_prompt.txt"
    state["file_list"].on_change(type("Event", (), {"control": type("Control", (), {"value": "04_page_1_prompt.txt"})()})())

    assert state["generate_selected_image_button"].visible is True


def test_output_page_defaults_to_run_status_when_latest_failed(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    v003 = episode_dir / "v003"
    v003.mkdir(parents=True, exist_ok=True)
    (v003 / "run_status.json").write_text(
        json.dumps({"status": "failed", "checkpoints": [], "failed": ["architect"], "errors": ["fatal"]}),
        encoding="utf-8",
    )
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["file_list"].value == "run_status.json"
    assert "status" in (state["preview"].value or "")


def test_output_page_campaign_switch_updates_episode_and_version(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)

    other_episode = campaigns_root / "other_camp" / "other-episode"
    other_v = other_episode / "v001"
    other_v.mkdir(parents=True, exist_ok=True)
    (other_episode / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "other-episode",
                "url": "https://example.com/other",
                "title": "Other Episode",
                "created_at": "2026-05-19T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (other_v / "run_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "checkpoints": ["scrape"],
                "failed": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (other_v / "04_page_1_prompt.txt").write_text("other prompt", encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["campaign_dropdown"].on_select(
        type(
            "CampaignChangeEvent",
            (),
            {"control": type("CampaignControl", (), {"value": "other_camp"})()},
        )()
    )

    assert state["campaign_dropdown"].value == "other_camp"
    assert state["episode_dropdown"].value == "other-episode"
    assert state["version_dropdown"].value == "v001"


def test_output_page_campaign_switch_uses_event_data(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)

    other_episode = campaigns_root / "other_camp" / "z-episode"
    older_episode = campaigns_root / "other_camp" / "a-episode"
    for ep_dir, slug, created in (
        (older_episode, "a-episode", "2026-05-18T00:00:00Z"),
        (other_episode, "z-episode", "2026-05-19T00:00:00Z"),
    ):
        v001 = ep_dir / "v001"
        v001.mkdir(parents=True, exist_ok=True)
        (ep_dir / "episode_meta.json").write_text(
            json.dumps(
                {
                    "slug": slug,
                    "url": f"https://example.com/{slug}",
                    "title": slug,
                    "created_at": created,
                }
            ),
            encoding="utf-8",
        )
        (v001 / "run_status.json").write_text(
            json.dumps({"status": "ok", "checkpoints": [], "failed": [], "errors": []}),
            encoding="utf-8",
        )
        (v001 / "04_page_1_prompt.txt").write_text(slug, encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["campaign_dropdown"].on_select(
        type(
            "CampaignChangeEvent",
            (),
            {
                "data": "other_camp",
                "control": type("CampaignControl", (), {"value": None})(),
            },
        )()
    )

    assert state["campaign_dropdown"].value == "other_camp"
    assert state["episode_dropdown"].value == "z-episode"
    assert state["version_dropdown"].value == "v001"


def test_output_page_campaign_switch_on_select_path(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    other_episode = campaigns_root / "other_camp" / "other-episode"
    other_v = other_episode / "v001"
    other_v.mkdir(parents=True, exist_ok=True)
    (other_episode / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "other-episode",
                "url": "https://example.com/other",
                "title": "Other Episode",
                "created_at": "2026-05-19T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (other_v / "run_status.json").write_text(
        json.dumps({"status": "ok", "checkpoints": [], "failed": [], "errors": []}),
        encoding="utf-8",
    )
    (other_v / "04_page_1_prompt.txt").write_text("other prompt", encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    event = type(
        "CampaignSelectEvent",
        (),
        {
            "data": "other_camp",
            "control": type("CampaignControl", (), {"value": "other_camp"})(),
        },
    )()

    handler = state["campaign_dropdown"].on_select or state["campaign_dropdown"].on_change
    handler(event)

    assert state["campaign_dropdown"].value == "other_camp"
    assert state["episode_dropdown"].value == "other-episode"
    assert state["version_dropdown"].value == "v001"
    assert "Loaded: other_camp / other-episode / v001" == state["output_status_text"].value


def test_output_page_lists_episodes_when_meta_slugs_collide(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    campaign_root = campaigns_root / "belowdown"
    live = campaign_root / "belowdown-ep-12"
    archive = campaign_root / "belowdown-ep-12_pre_entity_bible"
    newest = campaign_root / "the-vault-of-the-once-great-thief-ep-3"
    for episode_dir, slug, created_at in (
        (archive, "belowdown-ep-12", "2026-06-13T16:24:46.013591+00:00"),
        (live, "belowdown-ep-12", "2026-06-13T22:14:48.252712+00:00"),
        (newest, "the-vault-of-the-once-great-thief-ep-3", "2026-08-15T18:50:26.072604+00:00"),
    ):
        v001 = episode_dir / "v001"
        v001.mkdir(parents=True, exist_ok=True)
        (episode_dir / "episode_meta.json").write_text(
            json.dumps(
                {
                    "slug": slug,
                    "url": f"https://example.com/{episode_dir.name}",
                    "title": episode_dir.name,
                    "created_at": created_at,
                }
            ),
            encoding="utf-8",
        )
        (v001 / "run_status.json").write_text(
            json.dumps({"status": "ok", "checkpoints": [], "failed": [], "errors": []}),
            encoding="utf-8",
        )
        (v001 / "04_page_1_prompt.txt").write_text(episode_dir.name, encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    handler = state["campaign_dropdown"].on_select or state["campaign_dropdown"].on_change
    handler(
        type(
            "CampaignSelectEvent",
            (),
            {
                "data": "belowdown",
                "control": type("CampaignControl", (), {"value": "belowdown"})(),
            },
        )()
    )

    option_keys = [option.key for option in state["episode_dropdown"].options]
    assert option_keys == [
        "belowdown-ep-12_pre_entity_bible",
        "belowdown-ep-12",
        "the-vault-of-the-once-great-thief-ep-3",
    ]
    assert len(option_keys) == len(set(option_keys))
    assert state["episode_dropdown"].value == "the-vault-of-the-once-great-thief-ep-3"
    assert state["version_dropdown"].value == "v001"
    assert state["loading_ring"].visible is False


def test_output_page_shows_star_in_dropdown_for_starred_versions(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    v001_status = campaigns_root / "test_camp" / "episode-1" / "v001" / "run_status.json"
    payload = json.loads(v001_status.read_text(encoding="utf-8"))
    payload["starred"] = True
    payload["description"] = "the one I liked"
    v001_status.write_text(json.dumps(payload), encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    options = {option.key: option for option in state["version_dropdown"].options}
    assert options["v001"].leading_icon.icon == ft.Icons.STAR
    assert options["v001"].leading_icon.color == ft.Colors.GREEN_700
    assert options["v002"].leading_icon is None
    assert state["version_dropdown"].value == "v002"
    assert state["star_button"].visible is True
    assert state["star_button"].selected is False
    assert state["version_description_field"].visible is True
    assert state["version_description_field"].value == ""

    handler = state["version_dropdown"].on_select or state["version_dropdown"].on_change
    handler(
        type(
            "VersionSelectEvent",
            (),
            {
                "data": "v001",
                "control": type("VersionControl", (), {"value": "v001"})(),
            },
        )()
    )
    assert state["star_button"].selected is True
    assert state["version_description_field"].value == "the one I liked"


def test_output_page_clicking_star_toggles_favorite(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    v002 = campaigns_root / "test_camp" / "episode-1" / "v002"
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["version_dropdown"].value == "v002"
    assert state["star_button"].selected is False

    state["star_button"].on_click(None)

    on_disk = json.loads((v002 / "run_status.json").read_text(encoding="utf-8"))
    assert on_disk["starred"] is True
    assert on_disk["status"] == "partial"
    assert state["star_button"].selected is True
    assert state["version_dropdown"].value == "v002"
    v002_option = next(
        option for option in state["version_dropdown"].options if option.key == "v002"
    )
    assert v002_option.leading_icon.icon == ft.Icons.STAR
    assert v002_option.leading_icon.color == ft.Colors.GREEN_700

    state["star_button"].on_click(None)
    on_disk = json.loads((v002 / "run_status.json").read_text(encoding="utf-8"))
    assert on_disk["starred"] is False
    assert state["star_button"].selected is False


def test_output_page_clicking_star_replaces_frozen_dropdown_options(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    v002 = campaigns_root / "test_camp" / "episode-1" / "v002"
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    frozen_options = list(state["version_dropdown"].options)
    for option in frozen_options:
        object.__setattr__(option, "_frozen", True)

    state["star_button"].on_click(None)

    on_disk = json.loads((v002 / "run_status.json").read_text(encoding="utf-8"))
    assert on_disk["starred"] is True
    assert state["star_button"].selected is True
    assert state["version_dropdown"].value == "v002"
    v002_option = next(
        option for option in state["version_dropdown"].options if option.key == "v002"
    )
    assert v002_option.leading_icon.icon == ft.Icons.STAR
    assert v002_option.leading_icon.color == ft.Colors.GREEN_700
    assert v002_option is not frozen_options[-1]


def test_output_page_hides_star_and_note_for_working(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    working = campaigns_root / "test_camp" / "episode-1" / "working"
    working.mkdir()
    (working / "01_raw_text.json").write_text("{}", encoding="utf-8")
    (working / "run_status.json").write_text(
        json.dumps({"status": "ok", "checkpoints": [], "failed": [], "errors": []}),
        encoding="utf-8",
    )

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["star_button"].visible is True
    handler = state["version_dropdown"].on_select or state["version_dropdown"].on_change
    handler(
        type(
            "VersionSelectEvent",
            (),
            {
                "data": "working",
                "control": type("VersionControl", (), {"value": "working"})(),
            },
        )()
    )

    assert state["version_dropdown"].value == "working"
    assert state["star_button"].visible is False
    assert state["version_description_field"].visible is False
    working_status = json.loads((working / "run_status.json").read_text(encoding="utf-8"))
    assert "starred" not in working_status


def test_output_page_description_blur_saves_note(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    v002 = campaigns_root / "test_camp" / "episode-1" / "v002"
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["version_description_field"].value = "  swapped to vignette  "
    state["version_description_field"].on_blur(None)

    on_disk = json.loads((v002 / "run_status.json").read_text(encoding="utf-8"))
    assert on_disk["description"] == "swapped to vignette"
    assert on_disk["status"] == "partial"
    assert state["version_description_field"].value == "swapped to vignette"

    state["version_description_field"].value = "kept panel mode"
    state["version_description_field"].on_submit(None)
    on_disk = json.loads((v002 / "run_status.json").read_text(encoding="utf-8"))
    assert on_disk["description"] == "kept panel mode"


def test_output_page_shows_error_icon_for_incomplete_versions(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    v003 = episode_dir / "v003"
    v003.mkdir()
    (v003 / "01_raw_text.json").write_text("{}", encoding="utf-8")
    (v003 / "run_status.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "checkpoints": [],
                "failed": ["script"],
                "errors": ["script crashed"],
            }
        ),
        encoding="utf-8",
    )
    working = episode_dir / "working"
    working.mkdir()
    (working / "01_raw_text.json").write_text("{}", encoding="utf-8")
    (working / "run_status.json").write_text(
        json.dumps(
            {
                "status": "cancelled",
                "checkpoints": ["scrape"],
                "failed": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    options = {option.key: option for option in state["version_dropdown"].options}
    assert options["v001"].trailing_icon is None
    assert options["v002"].trailing_icon.icon == ft.Icons.WARNING
    assert options["v002"].trailing_icon.color == ft.Colors.AMBER_700
    assert options["v003"].trailing_icon.icon == ft.Icons.ERROR
    assert options["v003"].trailing_icon.color == ft.Colors.RED_700
    assert options["working"].trailing_icon.icon == ft.Icons.ERROR
    assert options["working"].trailing_icon.color == ft.Colors.RED_700


def test_output_page_starred_failed_version_shows_star_and_error(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    v001_status = campaigns_root / "test_camp" / "episode-1" / "v001" / "run_status.json"
    payload = json.loads(v001_status.read_text(encoding="utf-8"))
    payload["starred"] = True
    payload["status"] = "failed"
    v001_status.write_text(json.dumps(payload), encoding="utf-8")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    options = {option.key: option for option in state["version_dropdown"].options}
    assert options["v001"].leading_icon.icon == ft.Icons.STAR
    assert options["v001"].leading_icon.color == ft.Colors.GREEN_700
    assert options["v001"].trailing_icon.icon == ft.Icons.ERROR
    assert options["v001"].trailing_icon.color == ft.Colors.RED_700
    assert options["v002"].leading_icon is None
    assert options["v002"].trailing_icon.icon == ft.Icons.WARNING
    assert options["v002"].trailing_icon.color == ft.Colors.AMBER_700


def _add_version_image(
    version_dir: Path, name: str = "05_page_1.png", data: bytes = b"fake-png"
) -> Path:
    path = version_dir / "images" / "v001" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _option_trailing_icons(option) -> list:
    trailing = option.trailing_icon
    if trailing is None:
        return []
    controls = getattr(trailing, "controls", None)
    if controls is not None:
        return [control.icon for control in controls]
    return [trailing.icon]


def _fake_image_generator(monkeypatch):
    fake_generator = type(
        "FakeGenerator",
        (),
        {
            "generate_image": lambda self, prompt: f"img:{prompt}".encode(),
            "save_image": lambda self, image_bytes, output_path: (
                Path(output_path).parent.mkdir(parents=True, exist_ok=True),
                Path(output_path).write_bytes(image_bytes),
                Path(output_path),
            )[-1],
        },
    )()
    monkeypatch.setattr("gui.ImageGenerator", lambda model: fake_generator)
    return fake_generator


class _TaskPage(_FakePage):
    def run_task(self, task: object) -> None:
        asyncio.run(task())


def test_output_page_selecting_image_shows_uneditable_image_preview(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    image_path = _add_version_image(version_dir, data=b"comic-page-bytes")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_file(state, "images/v001/05_page_1.png")

    assert state["preview"].visible is False
    assert state["preview"].read_only is True
    assert state["save_file_button"].disabled is True
    assert state["reload_file_button"].disabled is True
    assert state["preview_image"].visible is True
    assert state["preview_image"].src == str(image_path)


def test_output_page_copy_image_copies_image_bytes(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    _add_version_image(version_dir, data=b"comic-page-bytes")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_file(state, "images/v001/05_page_1.png")
    asyncio.run(state["on_copy_content"](None))

    assert page.clipboard_image == b"comic-page-bytes"
    assert page.clipboard_text == ""


def test_output_page_selecting_text_after_image_restores_text_preview(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    _add_version_image(version_dir)

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_file(state, "images/v001/05_page_1.png")
    _select_output_file(state, "04_page_1_prompt.txt")

    assert state["preview"].visible is True
    assert state["preview_image"].visible is False
    assert state["preview"].value == "new prompt"


def test_output_page_generate_images_shows_generated_image(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    _fake_image_generator(monkeypatch)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    state["generate_images_button"].on_click(None)

    image_path = version_dir / "images" / "v001" / "05_page_1.png"
    assert state["file_list"].value == "images/v001/05_page_1.png"
    assert state["preview_image"].visible is True
    assert state["preview"].visible is False
    assert state["preview_image"].src == str(image_path)


def test_output_page_test_image_shows_generated_image(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    version_dir = campaigns_root / "test_camp" / "episode-1" / "v002"
    (version_dir / "04_page_2_prompt.txt").write_text("page two", encoding="utf-8")
    _fake_image_generator(monkeypatch)

    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_file(state, "04_page_2_prompt.txt")
    state["test_image_button"].on_click(None)

    image_path = version_dir / "images" / "v001" / "05_page_2.png"
    assert image_path.exists()
    assert state["file_list"].value == "images/v001/05_page_2.png"
    assert state["preview_image"].visible is True
    assert state["preview"].visible is False
    assert state["preview_image"].src == str(image_path)


def _output_page_on_v001(tmp_path, monkeypatch):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    v001 = campaigns_root / "test_camp" / "episode-1" / "v001"
    _fake_image_generator(monkeypatch)
    page = _TaskPage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)
    _select_output_version(state, "v001")
    assert state["version_dropdown"].value == "v001"
    return state, v001


def test_output_page_generate_images_keeps_selected_version(tmp_path, monkeypatch):
    state, v001 = _output_page_on_v001(tmp_path, monkeypatch)

    state["generate_images_button"].on_click(None)

    assert state["version_dropdown"].value == "v001"
    image_path = v001 / "images" / "v001" / "05_page_1.png"
    assert image_path.exists()
    assert state["file_list"].value == "images/v001/05_page_1.png"
    assert state["preview_image"].src == str(image_path)


def test_output_page_test_image_keeps_selected_version(tmp_path, monkeypatch):
    state, v001 = _output_page_on_v001(tmp_path, monkeypatch)
    _select_output_file(state, "04_page_1_prompt.txt")

    state["test_image_button"].on_click(None)

    assert state["version_dropdown"].value == "v001"
    image_path = v001 / "images" / "v001" / "05_page_1.png"
    assert image_path.exists()
    assert state["file_list"].value == "images/v001/05_page_1.png"
    assert state["preview_image"].src == str(image_path)


def test_output_page_regenerate_keeps_selected_version(tmp_path, monkeypatch):
    state, v001 = _output_page_on_v001(tmp_path, monkeypatch)
    _select_output_file(state, "04_page_1_prompt.txt")

    state["generate_selected_image_button"].on_click(None)

    assert state["version_dropdown"].value == "v001"
    image_path = v001 / "images" / "v001" / "05_page_1.png"
    assert image_path.exists()
    assert state["file_list"].value == "images/v001/05_page_1.png"
    assert state["preview_image"].src == str(image_path)


def test_output_page_stitch_keeps_selected_version(tmp_path, monkeypatch):
    state, v001 = _output_page_on_v001(tmp_path, monkeypatch)
    _add_version_image(v001, "05_page_1_panel_1.png")
    _add_version_image(v001, "05_page_1_panel_2.png")

    def _fake_stitch(panel_paths, output_path, aspect_ratio):
        Path(output_path).write_bytes(b"stitched")
        return Path(output_path)

    monkeypatch.setattr("gui.stitch_panel_images", _fake_stitch)
    _select_output_version(state, "v001")

    state["stitch_images_button"].on_click(None)

    assert state["version_dropdown"].value == "v001"
    assert (v001 / "images" / "v001" / "06_page_1.png").exists()


def test_output_page_episode_list_shows_image_icon_for_episodes_with_images(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    _add_version_image(campaigns_root / "test_camp" / "episode-1" / "v001")
    other = campaigns_root / "test_camp" / "other-episode"
    other.mkdir()
    (other / "episode_meta.json").write_text(
        json.dumps(
            {
                "slug": "other-episode",
                "url": "https://example.com/other",
                "title": "Other Episode",
                "created_at": "2026-05-19T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (other / "v001").mkdir()
    (other / "v001" / "run_status.json").write_text(
        json.dumps({"status": "ok", "checkpoints": [], "failed": [], "errors": []}),
        encoding="utf-8",
    )

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    options = {option.key: option for option in state["episode_dropdown"].options}
    assert options["episode-1"].trailing_icon.icon == ft.Icons.IMAGE
    assert options["other-episode"].trailing_icon is None


def test_output_page_version_list_shows_image_icon_next_to_status_icons(tmp_path):
    import flet as ft

    campaigns_root = _make_output_versions(tmp_path)
    episode_dir = campaigns_root / "test_camp" / "episode-1"
    _add_version_image(episode_dir / "v001")
    _add_version_image(episode_dir / "v002")

    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    options = {option.key: option for option in state["version_dropdown"].options}
    assert _option_trailing_icons(options["v001"]) == [ft.Icons.IMAGE]
    assert _option_trailing_icons(options["v002"]) == [ft.Icons.IMAGE, ft.Icons.WARNING]


_WORKING_EDIT_TOOLTIP = "Change to the working version to edit"


def _select_output_version(state: dict, version: str) -> None:
    handler = state["version_dropdown"].on_select or state["version_dropdown"].on_change
    handler(
        type(
            "VersionSelectEvent",
            (),
            {
                "data": version,
                "control": type("VersionControl", (), {"value": version})(),
            },
        )()
    )


def _select_output_file(state: dict, name: str) -> None:
    state["file_list"].value = name
    state["file_list"].on_change(
        type(
            "RadioChangeEvent",
            (),
            {"control": type("RadioControl", (), {"value": name})()},
        )()
    )


def _make_output_with_working(tmp_path: Path) -> Path:
    campaigns_root = _make_output_versions(tmp_path)
    working = campaigns_root / "test_camp" / "episode-1" / "working"
    working.mkdir()
    (working / "01_raw_text.json").write_text('{"a": 2}', encoding="utf-8")
    (working / "02_5_story_bible.txt").write_text("original bible\n", encoding="utf-8")
    (working / "04_page_1_prompt.txt").write_text("working prompt\n", encoding="utf-8")
    (working / "run_status.json").write_text(
        json.dumps({"status": "ok", "checkpoints": [], "failed": [], "errors": []}),
        encoding="utf-8",
    )
    return campaigns_root


def test_output_page_version_disables_save_and_reload_with_tooltip(tmp_path):
    import flet as ft

    campaigns_root = _make_output_with_working(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    assert state["version_dropdown"].value == "v002"
    assert state["save_file_button"].disabled is True
    assert state["reload_file_button"].disabled is True
    assert state["save_file_button"].tooltip == _WORKING_EDIT_TOOLTIP
    assert state["reload_file_button"].tooltip == _WORKING_EDIT_TOOLTIP
    assert state["preview"].read_only is True


def test_output_page_working_enables_save_and_reload(tmp_path):
    import flet as ft

    campaigns_root = _make_output_with_working(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_version(state, "working")
    _select_output_file(state, "02_5_story_bible.txt")

    assert state["version_dropdown"].value == "working"
    assert state["save_file_button"].disabled is False
    assert state["reload_file_button"].disabled is False
    assert state["preview"].read_only is False
    assert state["save_file_button"].tooltip in {None, ""}
    assert state["reload_file_button"].tooltip in {None, ""}


def test_output_page_switching_from_working_to_version_relocks_editor(tmp_path):
    import flet as ft

    campaigns_root = _make_output_with_working(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_version(state, "working")
    assert state["save_file_button"].disabled is False

    _select_output_version(state, "v002")
    assert state["save_file_button"].disabled is True
    assert state["reload_file_button"].disabled is True
    assert state["save_file_button"].tooltip == _WORKING_EDIT_TOOLTIP
    assert state["reload_file_button"].tooltip == _WORKING_EDIT_TOOLTIP
    assert state["preview"].read_only is True


def test_output_page_save_writes_working_file(tmp_path):
    import flet as ft

    campaigns_root = _make_output_with_working(tmp_path)
    working = campaigns_root / "test_camp" / "episode-1" / "working"
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_version(state, "working")
    _select_output_file(state, "02_5_story_bible.txt")
    state["preview"].value = "edited bible"
    state["on_save_file"](None)

    assert (working / "02_5_story_bible.txt").read_text(encoding="utf-8") == "edited bible\n"
    assert "Saved 02_5_story_bible.txt" in state["output_status_text"].value


def test_output_page_reload_discards_unsaved_edits(tmp_path):
    import flet as ft

    campaigns_root = _make_output_with_working(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_version(state, "working")
    _select_output_file(state, "02_5_story_bible.txt")
    assert "original bible" in state["preview"].value
    state["preview"].value = "unsaved draft"
    state["on_reload_file"](None)

    assert "original bible" in state["preview"].value
    assert "unsaved draft" not in state["preview"].value


def test_output_page_save_on_version_does_not_write(tmp_path):
    import flet as ft

    campaigns_root = _make_output_with_working(tmp_path)
    version_file = campaigns_root / "test_camp" / "episode-1" / "v002" / "01_raw_text.json"
    original = version_file.read_text(encoding="utf-8")
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_output_page(services, page, ft)

    _select_output_file(state, "01_raw_text.json")
    state["preview"].value = '{"tampered": true}'
    state["on_save_file"](None)

    assert version_file.read_text(encoding="utf-8") == original