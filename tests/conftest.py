from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import keyring
import llm_client
import settings_service


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """Keep pytest runs free from real credentials and local .env leakage."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama")

    monkeypatch.setattr(keyring, "get_password", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(keyring, "set_password", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(settings_service.keyring, "get_password", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings_service.keyring, "set_password", lambda *_args, **_kwargs: None)

    llm_client._ENV_LOADED = False
