from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import app_paths


def test_app_data_root_windows_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("COMIC_GENERATOR_APP_DATA_ROOT", raising=False)

    root = app_paths.app_data_root()

    assert root == tmp_path / "TTRPG_Comic_Generator"


def test_default_prompts_dir_respects_override(monkeypatch, tmp_path):
    custom_prompts = tmp_path / "prompts_override"
    custom_prompts.mkdir()
    monkeypatch.setenv("COMIC_GENERATOR_PROMPTS_DIR", str(custom_prompts))

    resolved = app_paths.default_prompts_dir()

    assert resolved == custom_prompts
