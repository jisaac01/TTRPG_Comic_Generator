"""Campaign prompt templates and art styles."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from art_styles import (
    bundled_art_direction_dir,
    campaign_art_direction_dir,
    default_art_direction_template_path,
    list_art_styles,
    parse_style_id,
    style_id,
)
from prompter import _normalize_art_template_object
from prompt_templates import (
    DEFAULT_PROMPTS_DIR,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
    STORY_ARCHITECT_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)
from web.schemas import (
    PromptContentResponse,
    PromptListItem,
    PromptListResponse,
    TextContentRequest,
)

router = APIRouter()

_TEMPLATE_KEYS: tuple[tuple[str, str], ...] = (
    ("story_architect_system", STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME),
    ("story_architect_user", STORY_ARCHITECT_USER_PROMPT_FILENAME),
    ("scriptwriter_system", SCRIPTWRITER_SYSTEM_PROMPT_FILENAME),
    ("scriptwriter_user", SCRIPTWRITER_USER_PROMPT_FILENAME),
    ("style_integrator_system", STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME),
    ("style_integrator_user", STYLE_INTEGRATOR_USER_PROMPT_FILENAME),
    ("page_prompt", PAGE_PROMPT_TEMPLATE_FILENAME),
)
_TEMPLATE_FILENAMES = dict(_TEMPLATE_KEYS)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/api/campaigns/{campaign}/prompts", response_model=PromptListResponse)
def list_prompts(campaign: str, request: Request) -> PromptListResponse:
    repository = request.app.state.services.repository
    items: list[PromptListItem] = []
    for style in list_art_styles(repository.campaigns_root, campaign):
        items.append(
            PromptListItem(
                key=style.id,
                label=style.label,
                exists=style.path.exists(),
                kind="art_style",
            )
        )
    prompts = repository.get_campaign_prompts(campaign)
    for key, filename in _TEMPLATE_KEYS:
        path = getattr(prompts, key)
        items.append(
            PromptListItem(
                key=key,
                label=filename,
                exists=path.exists(),
                kind="template",
            )
        )
    return PromptListResponse(prompts=items)


@router.post(
    "/api/campaigns/{campaign}/prompts/{key:path}/reset",
    response_model=PromptContentResponse,
)
def reset_prompt(campaign: str, key: str) -> PromptContentResponse:
    try:
        content = _default_prompt_content(key)
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    return PromptContentResponse(key=key, content=content)


@router.put(
    "/api/campaigns/{campaign}/prompts/{key:path}",
    response_model=PromptContentResponse,
)
def save_prompt(
    campaign: str, key: str, body: TextContentRequest, request: Request
) -> PromptContentResponse:
    repository = request.app.state.services.repository
    try:
        saved_key, content = _write_prompt(
            repository.campaigns_root, campaign, key, body.content
        )
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    return PromptContentResponse(key=saved_key, content=content)


@router.get(
    "/api/campaigns/{campaign}/prompts/{key:path}",
    response_model=PromptContentResponse,
)
def get_prompt(campaign: str, key: str, request: Request) -> PromptContentResponse:
    repository = request.app.state.services.repository
    try:
        content = _read_prompt(repository.campaigns_root, campaign, key)
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    return PromptContentResponse(key=key, content=content)


def _validate_art_template(text: str) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("Template must be a JSON object")
    _normalize_art_template_object(obj, source="editor")


def _default_prompt_content(key: str) -> str:
    if key in _TEMPLATE_FILENAMES:
        path = DEFAULT_PROMPTS_DIR / _TEMPLATE_FILENAMES[key]
        if not path.is_file():
            raise FileNotFoundError(f"prompt {key!r} was not found")
        return path.read_text(encoding="utf-8")
    try:
        _source, stem = parse_style_id(key)
    except ValueError as exc:
        raise FileNotFoundError(f"unknown prompt {key!r}") from exc
    path = bundled_art_direction_dir() / f"{stem}.json"
    if not path.is_file():
        path = default_art_direction_template_path()
    if not path.is_file():
        raise FileNotFoundError(f"prompt {key!r} was not found")
    return path.read_text(encoding="utf-8")


def _write_prompt(
    campaigns_root: Path, campaign: str, key: str, content: str
) -> tuple[str, str]:
    if key in _TEMPLATE_FILENAMES:
        path = campaigns_root / campaign / _TEMPLATE_FILENAMES[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return key, content

    try:
        _source, stem = parse_style_id(key)
    except ValueError as exc:
        raise FileNotFoundError(f"unknown prompt {key!r}") from exc
    _validate_art_template(content)
    path = campaign_art_direction_dir(campaigns_root, campaign) / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return style_id("campaign", stem), content


def _read_prompt(campaigns_root: Path, campaign: str, key: str) -> str:
    if key in _TEMPLATE_FILENAMES:
        filename = _TEMPLATE_FILENAMES[key]
        campaign_path = campaigns_root / campaign / filename
        if campaign_path.is_file():
            return campaign_path.read_text(encoding="utf-8")
        default_path = DEFAULT_PROMPTS_DIR / filename
        if default_path.is_file():
            return default_path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"prompt {key!r} was not found")

    try:
        source, stem = parse_style_id(key)
    except ValueError as exc:
        raise FileNotFoundError(f"unknown prompt {key!r}") from exc

    if source == "bundled":
        path = bundled_art_direction_dir() / f"{stem}.json"
    else:
        path = campaign_art_direction_dir(campaigns_root, campaign) / f"{stem}.json"
        if not path.is_file():
            bundled = bundled_art_direction_dir() / f"{stem}.json"
            if bundled.is_file():
                path = bundled
    if not path.is_file():
        raise FileNotFoundError(f"prompt {key!r} was not found")
    return path.read_text(encoding="utf-8")
