"""Read endpoints for episode versions, checkpoints, and images."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from repository_service import (
    IMAGE_FILE_SUFFIXES,
    WORKING_DIR_NAME,
    VersionInfo,
)
from web.schemas import (
    TextContentRequest,
    VersionFileContentResponse,
    VersionFileItem,
    VersionFileListResponse,
    VersionListResponse,
    VersionMetaUpdate,
    VersionResponse,
    VersionStatusResponse,
)

router = APIRouter()

_RUN_CONFIG_PUBLIC_KEYS = (
    "panel_count",
    "total_pages",
    "recap_version",
    "aspect_ratio",
    "generation_mode",
    "vignette",
    "cache_buster",
    "unstyled_prompts",
    "chat_mode",
    "pg13_mode",
    "art_style",
    "generate_images",
    "skip_style",
    "rerun_from",
    "stop_after",
)

_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


def _working_version(campaign: str, episode: str, request: Request) -> VersionResponse | None:
    repository = request.app.state.services.repository
    if not repository.has_working(campaign, episode):
        return None
    status = repository.run_status(campaign, episode, WORKING_DIR_NAME) or {}
    return VersionResponse(
        version=WORKING_DIR_NAME,
        label="working (editable)",
        status=status.get("status"),
        starred=False,
        description="",
        has_images=repository.version_has_images(campaign, episode, WORKING_DIR_NAME),
        editable=True,
    )


def _historical_version(info: VersionInfo, campaign: str, episode: str, request: Request) -> VersionResponse:
    repository = request.app.state.services.repository
    return VersionResponse(
        version=info.version,
        label=info.label,
        status=info.status,
        starred=info.starred,
        description=info.description,
        has_images=repository.version_has_images(campaign, episode, info.version),
        editable=False,
    )


def _public_status(status: dict) -> VersionStatusResponse:
    run_config = status.get("run_config")
    public_config: dict = {}
    if isinstance(run_config, dict):
        public_config = {
            key: run_config[key] for key in _RUN_CONFIG_PUBLIC_KEYS if key in run_config
        }
    return VersionStatusResponse(
        status=status.get("status"),
        version=status.get("version"),
        checkpoints=list(status.get("checkpoints") or []),
        failed=list(status.get("failed") or []),
        errors=list(status.get("errors") or []),
        warnings=list(status.get("warnings") or []),
        starred=bool(status.get("starred", False)),
        description=str(status.get("description") or ""),
        run_config=public_config,
    )


def _preview_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".json":
        return text
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


@router.get(
    "/api/campaigns/{campaign}/episodes/{episode}/versions",
    response_model=VersionListResponse,
)
def list_versions(campaign: str, episode: str, request: Request) -> VersionListResponse:
    repository = request.app.state.services.repository
    versions: list[VersionResponse] = []
    working = _working_version(campaign, episode, request)
    if working is not None:
        versions.append(working)
    versions.extend(
        _historical_version(info, campaign, episode, request)
        for info in repository.list_versions(campaign, episode)
    )
    return VersionListResponse(versions=versions)


@router.get(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/status",
    response_model=VersionStatusResponse,
)
def get_version_status(
    campaign: str, episode: str, version: str, request: Request
) -> VersionStatusResponse:
    repository = request.app.state.services.repository
    try:
        repository.resolve_version_file(campaign, episode, version, "run_status.json")
    except ValueError as exc:
        raise _http_error(exc) from exc
    status = repository.run_status(campaign, episode, version)
    if status is None:
        raise HTTPException(status_code=404, detail="run_status.json was not found")
    return _public_status(status)


@router.get(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/files",
    response_model=VersionFileListResponse,
)
def list_version_files(
    campaign: str, episode: str, version: str, request: Request
) -> VersionFileListResponse:
    repository = request.app.state.services.repository
    try:
        entries = repository.list_version_files(campaign, episode, version)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return VersionFileListResponse(
        files=[
            VersionFileItem(key=entry.key, kind=entry.kind, exists=entry.exists)
            for entry in entries
        ]
    )


@router.patch(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}",
    response_model=VersionResponse,
)
def patch_version(
    campaign: str,
    episode: str,
    version: str,
    body: VersionMetaUpdate,
    request: Request,
) -> VersionResponse:
    repository = request.app.state.services.repository
    try:
        info = repository.update_version_meta(
            campaign,
            episode,
            version,
            starred=body.starred,
            description=body.description,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    return _historical_version(info, campaign, episode, request)


@router.put(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/files/{key:path}",
    response_model=VersionFileContentResponse,
)
def save_version_file(
    campaign: str,
    episode: str,
    version: str,
    key: str,
    body: TextContentRequest,
    request: Request,
) -> VersionFileContentResponse:
    if version != WORKING_DIR_NAME:
        raise HTTPException(status_code=400, detail="only working files can be edited")
    repository = request.app.state.services.repository
    try:
        path = repository.resolve_version_file(campaign, episode, version, key)
    except ValueError as exc:
        raise _http_error(exc) from exc
    if path.suffix.lower() in IMAGE_FILE_SUFFIXES:
        raise HTTPException(status_code=400, detail="image files cannot be edited as text")
    text = body.content
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return VersionFileContentResponse(key=key, content=text)


@router.get(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/files/{key:path}",
    response_model=VersionFileContentResponse,
)
def get_version_file(
    campaign: str, episode: str, version: str, key: str, request: Request
) -> VersionFileContentResponse:
    repository = request.app.state.services.repository
    try:
        path = repository.resolve_version_file(campaign, episode, version, key)
    except ValueError as exc:
        raise _http_error(exc) from exc
    if path.suffix.lower() in IMAGE_FILE_SUFFIXES:
        raise HTTPException(status_code=400, detail="use the media endpoint for image files")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{key} was not found")
    return VersionFileContentResponse(key=key, content=_preview_text(path))


@router.get(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/media/{key:path}",
)
def get_version_media(
    campaign: str, episode: str, version: str, key: str, request: Request
) -> FileResponse:
    repository = request.app.state.services.repository
    try:
        path = repository.resolve_version_file(campaign, episode, version, key)
    except ValueError as exc:
        raise _http_error(exc) from exc
    suffix = path.suffix.lower()
    if suffix not in IMAGE_FILE_SUFFIXES:
        raise HTTPException(status_code=400, detail="not an image file")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{key} was not found")
    return FileResponse(path, media_type=_IMAGE_MEDIA_TYPES[suffix])
