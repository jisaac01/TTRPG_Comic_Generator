"""Image generation and stitching jobs for a version."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from image_generator import (
    ImageGenerator,
    append_image_generation_record,
    generate_prompt_images,
    latest_images_dir,
    prompt_sort_key,
    stitch_images_dir,
)
from web.schemas import ImageJobResponse, ImagePromptRequest

router = APIRouter()

_PROMPT_SUFFIX = "_prompt.txt"
_PROMPT_PREFIX = "04_page_"


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


def _relative(version_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(version_dir).as_posix()
    except ValueError:
        return path.name


def _run_image_settings(repository, campaign: str, episode: str, version: str) -> dict[str, str]:
    status = repository.run_status(campaign, episode, version) or {}
    config = status.get("run_config") if isinstance(status.get("run_config"), dict) else {}
    return {
        "aspect_ratio": str(config.get("aspect_ratio") or "3:2"),
        "generation_mode": str(config.get("generation_mode") or "page"),
    }


def _prompt_paths(version_dir: Path) -> list[Path]:
    return sorted(version_dir.glob("04_page_*_prompt.txt"), key=prompt_sort_key)


def _is_page_prompt(path: Path) -> bool:
    return path.name.startswith(_PROMPT_PREFIX) and path.name.endswith(_PROMPT_SUFFIX)


def _run_generate(
    *,
    request: Request,
    campaign: str,
    episode: str,
    version: str,
    prompt_paths: list[Path],
    new_folder: bool,
    source: str,
) -> ImageJobResponse:
    repository = request.app.state.services.repository
    try:
        version_dir = repository.version_directory(campaign, episode, version)
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    settings = _run_image_settings(repository, campaign, episode, version)
    model = request.app.state.services.settings.get_image_generation_model()
    stitch = settings["generation_mode"] == "panel"
    campaign_root = repository.campaigns_root / campaign
    try:
        result = generate_prompt_images(
            version_dir,
            prompt_paths,
            model=model,
            aspect_ratio=settings["aspect_ratio"],
            stitch=stitch,
            generator=ImageGenerator(model=model, aspect_ratio=settings["aspect_ratio"]),
            new_folder=new_folder,
            campaign_root=campaign_root if campaign_root.is_dir() else None,
        )
        append_image_generation_record(
            version_dir,
            model=model,
            images_dir=result.images_dir,
            files=result.generated_paths,
            source=source,
            errors=result.errors,
            character_ref_slugs=result.character_ref_slugs,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    return ImageJobResponse(
        source=source,
        images_dir=_relative(version_dir, result.images_dir),
        files=[_relative(version_dir, path) for path in result.generated_paths],
        stitched=[_relative(version_dir, path) for path in result.stitched_paths],
        errors=list(result.errors),
        character_ref_slugs=list(result.character_ref_slugs),
    )


@router.post(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/images/generate",
    response_model=ImageJobResponse,
)
def generate_all_images(
    campaign: str, episode: str, version: str, request: Request
) -> ImageJobResponse:
    repository = request.app.state.services.repository
    try:
        version_dir = repository.version_directory(campaign, episode, version)
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    return _run_generate(
        request=request,
        campaign=campaign,
        episode=episode,
        version=version,
        prompt_paths=_prompt_paths(version_dir),
        new_folder=True,
        source="generate_all",
    )


@router.post(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/images/generate-selected",
    response_model=ImageJobResponse,
)
def generate_selected_image(
    campaign: str,
    episode: str,
    version: str,
    body: ImagePromptRequest,
    request: Request,
) -> ImageJobResponse:
    if not body.prompt:
        raise HTTPException(status_code=400, detail="Select a page prompt to regenerate")
    repository = request.app.state.services.repository
    try:
        path = repository.resolve_version_file(campaign, episode, version, body.prompt)
    except ValueError as exc:
        raise _http_error(exc) from exc
    if not path.is_file() or not _is_page_prompt(path):
        raise HTTPException(status_code=400, detail="Select a page prompt to regenerate")
    return _run_generate(
        request=request,
        campaign=campaign,
        episode=episode,
        version=version,
        prompt_paths=[path],
        new_folder=False,
        source="regenerate_selected",
    )


@router.post(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/images/test",
    response_model=ImageJobResponse,
)
def generate_test_image(
    campaign: str,
    episode: str,
    version: str,
    request: Request,
    body: ImagePromptRequest | None = None,
) -> ImageJobResponse:
    repository = request.app.state.services.repository
    try:
        version_dir = repository.version_directory(campaign, episode, version)
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    prompt_key = body.prompt if body is not None else None
    if prompt_key:
        try:
            path = repository.resolve_version_file(campaign, episode, version, prompt_key)
        except ValueError as exc:
            raise _http_error(exc) from exc
        if not path.is_file() or not _is_page_prompt(path):
            raise HTTPException(status_code=400, detail="Select a page prompt to regenerate")
        prompt_paths = [path]
    else:
        prompt_paths = _prompt_paths(version_dir)
        if not prompt_paths:
            raise HTTPException(
                status_code=400, detail="No prompt files available to generate a test image"
            )
        prompt_paths = [prompt_paths[0]]
    return _run_generate(
        request=request,
        campaign=campaign,
        episode=episode,
        version=version,
        prompt_paths=prompt_paths,
        new_folder=False,
        source="test_image",
    )


@router.post(
    "/api/campaigns/{campaign}/episodes/{episode}/versions/{version}/images/stitch",
    response_model=ImageJobResponse,
)
def stitch_images(
    campaign: str, episode: str, version: str, request: Request
) -> ImageJobResponse:
    repository = request.app.state.services.repository
    try:
        version_dir = repository.version_directory(campaign, episode, version)
    except (ValueError, FileNotFoundError) as exc:
        raise _http_error(exc) from exc
    images_dir = latest_images_dir(version_dir)
    if images_dir is None:
        raise HTTPException(status_code=400, detail="No panel images available to stitch")
    settings = _run_image_settings(repository, campaign, episode, version)
    try:
        stitched, errors = stitch_images_dir(
            images_dir, aspect_ratio=settings["aspect_ratio"]
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    if not stitched and not errors:
        raise HTTPException(status_code=400, detail="No panel images available to stitch")
    return ImageJobResponse(
        source="stitch",
        images_dir=_relative(version_dir, images_dir),
        files=[],
        stitched=[_relative(version_dir, path) for path in stitched],
        errors=list(errors),
    )
