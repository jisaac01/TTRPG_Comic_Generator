from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import keyring

from settings_service import SettingsService
from model_defaults import DEFAULT_MODEL


def test_settings_service_persists_config_and_applies_environment(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.json"
    service = SettingsService(config_path=config_path)

    stored_passwords: dict[tuple[str, str], str] = {}

    def fake_get_password(service_name: str, username: str) -> str | None:
        return stored_passwords.get((service_name, username))

    def fake_set_password(service_name: str, username: str, password: str) -> None:
        stored_passwords[(service_name, username)] = password

    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "set_password", fake_set_password)

    assert service.get_default_model() == DEFAULT_MODEL
    assert service.get_ollama_base_url() == "http://localhost:11434/v1"

    service.set_default_model("gemini-3.1-pro")
    service.set_ollama_base_url("http://localhost:11434/v1")
    service.set_gemini_api_key("secret-key")

    assert service.get_default_model() == "gemini-3.1-pro"
    assert service.get_ollama_base_url() == "http://localhost:11434/v1"
    assert service.get_gemini_api_key() == "secret-key"

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    service.apply_to_environment()

    assert os.environ["GEMINI_API_KEY"] == "secret-key"
    assert os.environ["OLLAMA_BASE_URL"] == "http://localhost:11434/v1"
