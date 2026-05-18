from __future__ import annotations

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
    _validate_art_template,
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
        self.clipboard_text = ""

    def add(self, control: object) -> None:
        self.controls.append(control)

    def update(self) -> None:
        self.update_calls += 1

    def set_clipboard(self, value: str) -> None:
        self.clipboard_text = value


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


# ---------------------------------------------------------------------------
# Phase 5: Prompt workspace
# ---------------------------------------------------------------------------

def _make_campaign_prompts(tmp_path: Path, campaign: str = "test_camp") -> Path:
    """Create a minimal campaign directory with all 8 prompt files."""
    from prompter import ART_DIRECTION_TEMPLATE_FIELDS

    camp_dir = tmp_path / "campaigns" / campaign
    camp_dir.mkdir(parents=True)

    art = {name: f"value for {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    (camp_dir / "art_direction_template.json").write_text(json.dumps(art), encoding="utf-8")
    for filename in (
        "master_beater_system.txt",
        "master_beater_user.txt",
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
    (v001 / "04_page_prompt.txt").write_text("old prompt", encoding="utf-8")
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
    (v002 / "04_page_prompt.txt").write_text("new prompt", encoding="utf-8")
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
    # 8 file radio buttons should be present
    assert len(state["file_list"].content.controls) == 8


def test_prompt_page_load_reads_file_into_editor(tmp_path):
    import flet as ft
    from prompt_templates import DEFAULT_PROMPTS_DIR

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

    expected = (DEFAULT_PROMPTS_DIR / "scriptwriter_system.txt").read_text(encoding="utf-8")
    assert state["editor"].value == expected


def test_prompt_page_radio_on_change_uses_control_value(tmp_path):
    import flet as ft
    from prompt_templates import DEFAULT_PROMPTS_DIR

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
    expected = (DEFAULT_PROMPTS_DIR / "scriptwriter_system.txt").read_text(encoding="utf-8")
    assert state["editor"].value == expected


def test_prompt_page_default_source_is_top_level_before_any_run(tmp_path):
    import flet as ft
    from prompt_templates import DEFAULT_PROMPTS_DIR

    campaigns_root = _make_campaign_prompts(tmp_path)
    page = _FakePage()
    services = _prompt_services(campaigns_root)
    _view, state = build_prompt_page(services, page, ft)

    campaign_file = campaigns_root / "test_camp" / "scriptwriter_system.txt"
    campaign_file.write_text("campaign override", encoding="utf-8")
    expected = (DEFAULT_PROMPTS_DIR / "scriptwriter_system.txt").read_text(encoding="utf-8")

    event = type(
        "RadioChangeEvent",
        (),
        {"control": type("RadioControl", (), {"value": "scriptwriter_system"})()},
    )()
    state["file_list"].on_change(event)

    assert state["editor"].value == expected


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

    target = campaigns_root / "test_camp" / "art_direction_template.json"
    original = target.read_text(encoding="utf-8")
    state["selected_key"][0] = "art_direction_template"
    state["paths"]["art_direction_template"] = target
    state["editor"].value = '{"base_style": "cool"}'  # missing fields
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

    target = campaigns_root / "test_camp" / "art_direction_template.json"
    valid_art = {name: f"updated {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    state["selected_key"][0] = "art_direction_template"
    state["paths"]["art_direction_template"] = target
    state["editor"].value = json.dumps(valid_art)
    state["on_save"](None)

    assert state["validation_text"].value == ""
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["base_style"] == "updated base_style"


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


def test_validate_art_template_passes_valid():
    from prompter import ART_DIRECTION_TEMPLATE_FIELDS

    art = {name: f"val {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    assert _validate_art_template(json.dumps(art)) is None


def test_validate_art_template_fails_bad_json():
    err = _validate_art_template("{not json}")
    assert err is not None and "JSON" in err


def test_validate_art_template_fails_missing_fields():
    err = _validate_art_template('{"base_style": "cool"}')
    assert err is not None and "Missing" in err


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
    assert "04_page_prompt.txt" in labels


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