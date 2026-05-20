from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app_paths import default_prompts_dir


DEFAULT_PROMPTS_DIR = default_prompts_dir()
MASTER_BEATER_SYSTEM_PROMPT_FILENAME = "master_beater_system.txt"
MASTER_BEATER_USER_PROMPT_FILENAME = "master_beater_user.txt"
SCRIPTWRITER_SYSTEM_PROMPT_FILENAME = "scriptwriter_system.txt"
SCRIPTWRITER_USER_PROMPT_FILENAME = "scriptwriter_user.txt"
STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME = "style_integrator_system.txt"
STYLE_INTEGRATOR_USER_PROMPT_FILENAME = "style_integrator_user.txt"
PAGE_PROMPT_TEMPLATE_FILENAME = "page_prompt.txt"
PROMPT_TEMPLATE_FILENAMES = (
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
    PAGE_PROMPT_TEMPLATE_FILENAME,
)


@lru_cache(maxsize=None)
def _load_prompt_template_from_path(path_text: str) -> str:
    return Path(path_text).read_text(encoding="utf-8")


def resolve_prompt_template_path(
    name: str | None = None,
    template_path: Path | None = None,
) -> Path:
    if template_path is not None:
        return template_path
    if name is None:
        raise ValueError("Either name or template_path must be provided.")
    return DEFAULT_PROMPTS_DIR / name


def load_prompt_template(
    name: str | None = None,
    template_path: Path | None = None,
) -> str:
    path = resolve_prompt_template_path(name=name, template_path=template_path)
    return _load_prompt_template_from_path(str(path))


def render_prompt_template(
    name: str | None = None,
    template_path: Path | None = None,
    **values: str | int,
) -> str:
    return load_prompt_template(name=name, template_path=template_path).format(**values)