"""Settings and model catalog endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from model_catalog import (
    deprecated_model_warnings,
    load_catalog,
    refresh_model_catalog,
)
from web.schemas import SettingsResponse, SettingsUpdate

router = APIRouter()


def _catalog_path(request: Request) -> Path:
    return request.app.state.catalog_path


def _mask_secret(value: str | None) -> tuple[bool, str]:
    if not value:
        return False, ""
    if len(value) <= 4:
        return True, "•" * len(value)
    return True, "•" * (len(value) - 4) + value[-4:]


def _settings_payload(request: Request, *, fetch_error: str | None = None) -> SettingsResponse:
    settings = request.app.state.services.settings
    catalog = load_catalog(_catalog_path(request))
    configured, masked = _mask_secret(settings.get_gemini_api_key())
    default_model = settings.get_default_model()
    image_model = settings.get_image_generation_model()
    warnings = deprecated_model_warnings(
        text_model=default_model,
        image_model=image_model,
        catalog=catalog,
    )
    return SettingsResponse(
        gemini_api_key_configured=configured,
        gemini_api_key_masked=masked,
        default_model=default_model,
        image_generation_model=image_model,
        text_models=catalog.text_models,
        image_models=catalog.image_models,
        warnings=warnings,
        fetch_error=fetch_error if fetch_error is not None else catalog.fetch_error,
    )


@router.get("/api/settings", response_model=SettingsResponse)
def get_settings(request: Request) -> SettingsResponse:
    return _settings_payload(request)


@router.put("/api/settings", response_model=SettingsResponse)
def put_settings(body: SettingsUpdate, request: Request) -> SettingsResponse:
    settings = request.app.state.services.settings
    if body.gemini_api_key:
        settings.set_gemini_api_key(body.gemini_api_key)
    if body.default_model is not None:
        settings.set_default_model(body.default_model)
    if body.image_generation_model is not None:
        settings.set_image_generation_model(body.image_generation_model)
    settings.apply_to_environment()
    return _settings_payload(request)


@router.post("/api/settings/refresh-models", response_model=SettingsResponse)
def refresh_models(request: Request) -> SettingsResponse:
    settings = request.app.state.services.settings
    api_key = settings.get_gemini_api_key()
    if not api_key:
        payload = _settings_payload(request)
        payload.warnings = [
            "No Gemini API key; using local model list",
            *payload.warnings,
        ]
        return payload
    catalog = refresh_model_catalog(_catalog_path(request), api_key)
    return _settings_payload(request, fetch_error=catalog.fetch_error)
