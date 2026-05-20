"""Application path resolution for dev and packaged desktop builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "TTRPG_Comic_Generator"
CAMPAIGNS_DIRNAME = "campaigns"
PROMPTS_DIRNAME = "prompts"
CONFIG_FILENAME = "settings.json"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    """Return root directory where bundled app resources live."""
    if _is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return project_root()


def app_data_root() -> Path:
    """Return user-writable application data directory."""
    configured = os.environ.get("COMIC_GENERATOR_APP_DATA_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    return base / APP_NAME


def default_campaigns_root() -> Path:
    configured = os.environ.get("COMIC_GENERATOR_CAMPAIGNS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return app_data_root() / CAMPAIGNS_DIRNAME


def default_config_path() -> Path:
    configured = os.environ.get("COMIC_GENERATOR_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return app_data_root() / CONFIG_FILENAME


def default_prompts_dir() -> Path:
    configured = os.environ.get("COMIC_GENERATOR_PROMPTS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()

    candidates = [
        resource_root() / PROMPTS_DIRNAME,
        project_root() / PROMPTS_DIRNAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
