"""Persistent settings access for API credentials and default runtime configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

import keyring

from model_defaults import DEFAULT_MODEL


KEYRING_SERVICE_NAME = "TTRPG_Comic_Generator"
KEYRING_USERNAME = "gemini_api_key"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_CONFIG_PATH = Path.home() / ".comic_generator_config.json"


class SettingsService:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or DEFAULT_CONFIG_PATH

    def get_gemini_api_key(self) -> str | None:
        return keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME)

    def set_gemini_api_key(self, key: str) -> None:
        keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_USERNAME, key)

    def get_ollama_base_url(self) -> str:
        return self._load_config().get("ollama_base_url", DEFAULT_OLLAMA_BASE_URL)

    def set_ollama_base_url(self, url: str) -> None:
        config = self._load_config()
        config["ollama_base_url"] = url
        self._save_config(config)

    def get_default_model(self) -> str:
        return self._load_config().get("default_model", DEFAULT_MODEL)

    def set_default_model(self, model: str) -> None:
        config = self._load_config()
        config["default_model"] = model
        self._save_config(config)

    def apply_to_environment(self) -> None:
        api_key = self.get_gemini_api_key()
        if api_key is not None:
            os.environ["GEMINI_API_KEY"] = api_key
        os.environ["OLLAMA_BASE_URL"] = self.get_ollama_base_url()

    def _load_config(self) -> dict[str, str]:
        if not self.config_path.exists():
            return {}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _save_config(self, config: dict[str, str]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )