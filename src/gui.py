"""Flet GUI shell for TTRPG Comic Generator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline_events import PipelineEventUnion
from repository_service import RepositoryService
from run_controller import RunController
from settings_service import SettingsService

try:
    import flet as ft
except ImportError:  # pragma: no cover - handled by smoke test skip path
    ft = None


EVENT_LOG_LIMIT = 100


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


def create_run_page(_ft: Any) -> Any:
    return _ft.Container(
        content=_ft.Text("Run workspace coming next", size=18, weight=_ft.FontWeight.W_600),
        padding=16,
    )


def create_prompt_page(_ft: Any) -> Any:
    return _ft.Container(
        content=_ft.Text("Prompts workspace coming next", size=18, weight=_ft.FontWeight.W_600),
        padding=16,
    )


def create_output_page(_ft: Any) -> Any:
    return _ft.Container(
        content=_ft.Text("Output workspace coming next", size=18, weight=_ft.FontWeight.W_600),
        padding=16,
    )


def format_log_line(source: str, message: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"{timestamp} [{source}] {message}"


def append_log_line(event_log: Any, source: str, message: str, _ft: Any) -> None:
    event_log.controls.append(_ft.Text(format_log_line(source, message), selectable=True, size=12))
    if len(event_log.controls) > EVENT_LOG_LIMIT:
        event_log.controls[:] = event_log.controls[-EVENT_LOG_LIMIT:]


def append_pipeline_event(event_log: Any, event: PipelineEventUnion, _ft: Any) -> None:
    payload = event.to_dict()
    message = payload.get("message") or payload.get("warning") or payload.get("error") or payload["type"]
    append_log_line(event_log, "Run", str(message), _ft)


def open_settings_dialog(page: Any, dialog: Any) -> None:
    page.dialog = dialog
    dialog.open = True
    page.update()


def close_settings_dialog(page: Any, dialog: Any) -> None:
    dialog.open = False
    page.update()


def build_main_layout(page: Any, services: AppServices) -> dict[str, Any]:
    if ft is None:
        raise RuntimeError("flet is not installed. Install flet to use the GUI.")

    page.title = "TTRPG Comic Generator"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 16

    event_log = ft.ListView(expand=True, auto_scroll=True, spacing=4, height=180)

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

    run_view = create_run_page(ft)
    prompt_view = create_prompt_page(ft)
    output_view = create_output_page(ft)
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
            ft.Text("Event Log", weight=ft.FontWeight.W_600),
            event_log,
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
        "prompt_view": prompt_view,
        "output_view": output_view,
        "event_log": event_log,
        "settings_button": settings_button,
        "settings_dialog": settings_dialog,
        "status_text": status_text,
    }


def main(page: Any) -> None:
    services = create_services()
    controls = build_main_layout(page, services)

    event_log = controls["event_log"]

    def gui_event_callback(event: PipelineEventUnion) -> None:
        append_pipeline_event(event_log, event, ft)
        page.update()


def run() -> None:
    if ft is None:
        raise RuntimeError("flet is not installed. Install flet to run src/gui.py.")
    ft.run(main)


if __name__ == "__main__":
    run()