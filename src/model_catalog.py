"""Local Gemini model catalog: fetch, additive cache, and deprecation warnings."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from model_defaults import SEED_IMAGE_MODELS, SEED_TEXT_MODELS

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
FETCH_TIMEOUT_SECONDS = 15
MAX_PAGES = 20
_SKIP_NAME_RE = re.compile(
    r"(tts|live|embed|gemma|robot|computer-use|aqa|imagen)",
    re.IGNORECASE,
)
_DATED_SNAPSHOT_RE = re.compile(r"-preview-\d{2}-\d{4}$")
_NUMBERED_REVISION_RE = re.compile(r"-\d{3}$")


@dataclass
class ModelCatalog:
    text_models: list[str]
    image_models: list[str]
    live_text_models: list[str] | None = None
    live_image_models: list[str] | None = None
    fetch_error: str | None = field(default=None, compare=False)


def seed_catalog() -> ModelCatalog:
    return ModelCatalog(
        text_models=list(SEED_TEXT_MODELS),
        image_models=list(SEED_IMAGE_MODELS),
    )


def normalize_model_id(name: str) -> str:
    if name.startswith("models/"):
        return name[len("models/") :]
    return name


def _is_image_model_id(model_id: str) -> bool:
    lower = model_id.lower()
    return "image" in lower or lower.startswith("imagen-")


def _is_gemini_family(model_id: str) -> bool:
    lower = model_id.lower()
    return lower.startswith("gemini-") or lower.startswith("imagen-")


@dataclass(frozen=True)
class ModelPricing:
    """Standard-tier note from https://ai.google.dev/gemini-api/docs/pricing.

    Gemini's models.list API does not include prices; this table is local.
    """

    free_tier: bool
    note: str


# Longest prefixes are matched first so lite-image wins over lite, etc.
_MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-3.7-flash": ModelPricing(True, "free; paid $0.75/$3.75 per 1M"),
    "gemini-3.6-flash": ModelPricing(True, "free; paid $0.75/$3.75 per 1M"),
    "gemini-3.5-flash-lite": ModelPricing(True, "free; paid $0.30/$2.50 per 1M"),
    "gemini-3.5-flash": ModelPricing(True, "free; paid $1.50/$9.00 per 1M"),
    "gemini-3.1-flash-lite-image": ModelPricing(False, "paid ~$0.034/image"),
    "gemini-3.1-flash-lite": ModelPricing(True, "free; paid $0.25/$1.50 per 1M"),
    "gemini-3.1-flash-image": ModelPricing(False, "paid ~$0.067/image"),
    "gemini-3.1-pro-preview": ModelPricing(False, "paid $2.00/$12.00 per 1M"),
    "gemini-3-flash-preview": ModelPricing(True, "free; paid $0.50/$3.00 per 1M"),
    "gemini-3-pro-image": ModelPricing(False, "paid ~$0.134/image"),
    "gemini-2.5-flash-image": ModelPricing(False, "paid ~$0.039/image"),
    "gemini-2.5-flash-lite": ModelPricing(True, "free; paid $0.10/$0.40 per 1M"),
    "gemini-2.5-flash": ModelPricing(True, "free; paid $0.30/$2.50 per 1M"),
    "gemini-2.5-pro": ModelPricing(True, "free; paid $1.25/$10.00 per 1M"),
}
_PRICING_PREFIXES = tuple(sorted(_MODEL_PRICING, key=len, reverse=True))


def pricing_for(model_id: str) -> ModelPricing | None:
    for prefix in _PRICING_PREFIXES:
        if model_id == prefix or model_id.startswith(prefix + "-"):
            return _MODEL_PRICING[prefix]
    return None


# Longest prefixes first so lite-image wins over image, etc.
_INPUT_IMAGE_CAPS: dict[str, int] = {
    "gemini-3.1-flash-lite-image": 4,
    "gemini-3.1-flash-image": 4,
    "gemini-3-pro-image": 5,
    "gemini-2.5-flash-image": 3,
}
_INPUT_IMAGE_CAP_PREFIXES = tuple(sorted(_INPUT_IMAGE_CAPS, key=len, reverse=True))
DEFAULT_INPUT_IMAGE_CAP = 3


def input_image_cap(model_id: str) -> int:
    """Max character-reference images to attach for this image model."""
    for prefix in _INPUT_IMAGE_CAP_PREFIXES:
        if model_id == prefix or model_id.startswith(prefix + "-"):
            return _INPUT_IMAGE_CAPS[prefix]
    return DEFAULT_INPUT_IMAGE_CAP


def _should_keep_listed_model(model_id: str) -> bool:
    if _is_image_model_id(model_id):
        return model_id.startswith("gemini-")
    if _SKIP_NAME_RE.search(model_id):
        return False
    if _DATED_SNAPSHOT_RE.search(model_id) or _NUMBERED_REVISION_RE.search(model_id):
        return False
    return True


def _generation_methods(model: dict[str, Any]) -> list[str]:
    methods = model.get("supportedGenerationMethods") or model.get("supported_actions") or []
    return [str(method) for method in methods]


def classify_gemini_models(models: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    text_models: list[str] = []
    image_models: list[str] = []
    seen_text: set[str] = set()
    seen_image: set[str] = set()

    for model in models:
        raw_name = str(model.get("name") or model.get("id") or "").strip()
        model_id = normalize_model_id(raw_name)
        if not model_id or not _should_keep_listed_model(model_id):
            continue
        if _is_image_model_id(model_id):
            if model_id not in seen_image:
                image_models.append(model_id)
                seen_image.add(model_id)
            continue
        if "embed" in model_id.lower():
            continue
        methods = _generation_methods(model)
        is_text = "generateContent" in methods or (
            not methods and model_id.startswith("gemini-")
        )
        if is_text and model_id not in seen_text:
            text_models.append(model_id)
            seen_text.add(model_id)

    return text_models, image_models


def _unique_extend(existing: list[str], incoming: list[str]) -> list[str]:
    seen = set(existing)
    merged = list(existing)
    for model_id in incoming:
        if model_id not in seen:
            merged.append(model_id)
            seen.add(model_id)
    return merged


def merge_catalog(
    existing: ModelCatalog,
    live_text: list[str],
    live_image: list[str],
) -> ModelCatalog:
    return ModelCatalog(
        text_models=_unique_extend(existing.text_models, live_text),
        image_models=_unique_extend(existing.image_models, live_image),
        live_text_models=list(live_text),
        live_image_models=list(live_image),
    )


def model_is_deprecated(
    model_id: str,
    known: list[str],
    live: list[str] | None,
) -> bool:
    if live is None:
        return False
    if model_id in live:
        return False
    if model_id in known:
        return True
    return _is_gemini_family(model_id)


def option_label(model_id: str, live: list[str] | None) -> str:
    parts: list[str] = []
    pricing = pricing_for(model_id)
    if pricing is not None:
        parts.append(pricing.note)
    if live is not None and model_id not in live and _is_gemini_family(model_id):
        parts.append("deprecated")
    if not parts:
        return model_id
    return f"{model_id} ({'; '.join(parts)})"


def deprecated_model_warnings(
    *,
    text_model: str,
    image_model: str,
    catalog: ModelCatalog,
) -> list[str]:
    warnings: list[str] = []
    if text_model and model_is_deprecated(
        text_model, catalog.text_models, catalog.live_text_models
    ):
        warnings.append(
            f"Default model '{text_model}' is no longer on Gemini's available list."
        )
    if image_model and model_is_deprecated(
        image_model, catalog.image_models, catalog.live_image_models
    ):
        warnings.append(
            f"Image generation model '{image_model}' is no longer on Gemini's available list."
        )
    return warnings


def load_catalog(path: Path | None) -> ModelCatalog:
    if path is None or not path.exists():
        return seed_catalog()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Model catalog must be a JSON object: {path}")
    text_models = data.get("text_models")
    image_models = data.get("image_models")
    if not isinstance(text_models, list) or not isinstance(image_models, list):
        raise ValueError(f"Model catalog is missing text_models/image_models: {path}")
    live_text = data.get("live_text_models")
    live_image = data.get("live_image_models")
    return ModelCatalog(
        text_models=[str(model) for model in text_models],
        image_models=[str(model) for model in image_models],
        live_text_models=None if live_text is None else [str(model) for model in live_text],
        live_image_models=None if live_image is None else [str(model) for model in live_image],
    )


def save_catalog(path: Path, catalog: ModelCatalog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "text_models": catalog.text_models,
        "image_models": catalog.image_models,
        "live_text_models": catalog.live_text_models,
        "live_image_models": catalog.live_image_models,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_gemini_models(
    api_key: str,
    *,
    urlopen: Callable[..., Any] | None = None,
) -> tuple[list[str], list[str]]:
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required to list models")

    opener = urlopen or urllib.request.urlopen
    collected: list[dict[str, Any]] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        params = {"pageSize": "1000"}
        if page_token:
            params["pageToken"] = page_token
        url = f"{GEMINI_MODELS_URL}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={"x-goog-api-key": api_key},
        )
        try:
            with opener(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OSError(f"Gemini models.list failed ({exc.code}): {body}") from exc
        collected.extend(payload.get("models") or [])
        page_token = payload.get("nextPageToken") or None
        if not page_token:
            break
    return classify_gemini_models(collected)


def refresh_model_catalog(path: Path | None, api_key: str | None) -> ModelCatalog:
    catalog = load_catalog(path)
    if api_key:
        try:
            live_text, live_image = fetch_gemini_models(api_key)
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            catalog.fetch_error = str(exc)
            return catalog
        catalog = merge_catalog(catalog, live_text, live_image)
    if path is not None:
        save_catalog(path, catalog)
    return catalog
