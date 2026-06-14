from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import llm_client


def test_build_openai_client_does_not_load_dotenv_during_pytest(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=fake-test-key\n", encoding="utf-8")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_llm_client.py::test_build_openai_client_does_not_load_dotenv_during_pytest")

    llm_client._ENV_LOADED = False

    with pytest.raises(EnvironmentError, match="GEMINI_API_KEY"):
        llm_client.build_openai_client("gemini-3.1-flash-lite")
