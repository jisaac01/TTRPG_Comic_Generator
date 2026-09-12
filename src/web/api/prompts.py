"""Read endpoints for campaign prompt templates and art styles."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from art_styles import (
    bundled_art_direction_dir,
    campaign_art_direction_dir,
    list_art_styles,
    parse_style_id,
)
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
from web.schemas import PromptContentResponse, PromptListItem, PromptListResponse

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
