from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from model_catalog import (
    ModelCatalog,
    classify_gemini_models,
    deprecated_model_warnings,
    fetch_gemini_models,
    input_image_cap,
    load_catalog,
    merge_catalog,
    model_is_deprecated,
    option_label,
    pricing_for,
    refresh_model_catalog,
    save_catalog,
    seed_catalog,
)
from model_defaults import DEFAULT_IMAGE_GENERATION_MODEL, DEFAULT_MODEL, SEED_IMAGE_MODELS, SEED_TEXT_MODELS


def test_seed_catalog_includes_current_defaults():
    catalog = seed_catalog()

    assert DEFAULT_MODEL in catalog.text_models
    assert DEFAULT_IMAGE_GENERATION_MODEL in catalog.image_models
    assert catalog.text_models == list(SEED_TEXT_MODELS)
    assert catalog.image_models == list(SEED_IMAGE_MODELS)
    assert len(catalog.text_models) > 1
    assert "gemini-3.1-flash-lite-image" in catalog.image_models
    assert catalog.live_text_models is None
    assert catalog.live_image_models is None


def test_classify_gemini_models_splits_text_and_image():
    text_models, image_models = classify_gemini_models(
        [
            {
                "name": "models/gemini-3.1-flash-lite",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            },
            {
                "name": "models/gemini-3.1-flash-image",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-3.1-pro-preview",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-embedding-001",
                "supportedGenerationMethods": ["embedContent"],
            },
            {
                "name": "models/gemini-2.5-flash-preview-tts",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-2.5-flash-preview-09-2025",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/imagen-4.0-generate-001",
                "supportedGenerationMethods": ["predict"],
            },
        ]
    )

    assert text_models == ["gemini-3.1-flash-lite", "gemini-3.1-pro-preview"]
    assert image_models == ["gemini-3.1-flash-image"]


def test_merge_catalog_only_adds_models():
    existing = ModelCatalog(
        text_models=["gemini-old-text", "gemini-3.1-flash-lite"],
        image_models=["gemini-old-image", "gemini-2.5-flash-image"],
        live_text_models=["gemini-old-text"],
        live_image_models=["gemini-old-image"],
    )

    merged = merge_catalog(
        existing,
        live_text=["gemini-3.1-flash-lite", "gemini-3.1-pro"],
        live_image=["gemini-3.1-flash-image"],
    )

    assert merged.text_models == [
        "gemini-old-text",
        "gemini-3.1-flash-lite",
        "gemini-3.1-pro",
    ]
    assert merged.image_models == [
        "gemini-old-image",
        "gemini-2.5-flash-image",
        "gemini-3.1-flash-image",
    ]
    assert merged.live_text_models == ["gemini-3.1-flash-lite", "gemini-3.1-pro"]
    assert merged.live_image_models == ["gemini-3.1-flash-image"]
    assert "gemini-old-text" not in merged.live_text_models
    assert "gemini-old-image" not in merged.live_image_models


def test_model_is_deprecated_only_after_successful_fetch():
    assert model_is_deprecated("gemini-old-image", known=["gemini-old-image"], live=None) is False
    assert model_is_deprecated("gemini-old-image", known=["gemini-old-image"], live=[]) is True
    assert model_is_deprecated(
        "gemini-2.5-flash-image",
        known=["gemini-2.5-flash-image"],
        live=["gemini-3.1-flash-image"],
    )
    assert not model_is_deprecated(
        "gemini-3.1-flash-image",
        known=["gemini-3.1-flash-image"],
        live=["gemini-3.1-flash-image"],
    )


def test_custom_ollama_model_is_not_deprecated():
    assert not model_is_deprecated(
        "qwen3:8b",
        known=["gemini-3.1-flash-lite"],
        live=["gemini-3.1-flash-lite"],
    )


def test_selected_gemini_model_missing_from_live_list_is_deprecated():
    assert model_is_deprecated(
        "gemini-gone",
        known=["gemini-3.1-flash-lite"],
        live=["gemini-3.1-flash-lite"],
    )


def test_deprecated_model_warnings_cover_text_and_image_selections():
    catalog = ModelCatalog(
        text_models=["gemini-3.1-flash-lite", "gemini-gone"],
        image_models=["gemini-2.5-flash-image", "gemini-3.1-flash-image"],
        live_text_models=["gemini-3.1-flash-lite"],
        live_image_models=["gemini-3.1-flash-image"],
    )

    warnings = deprecated_model_warnings(
        text_model="gemini-gone",
        image_model="gemini-2.5-flash-image",
        catalog=catalog,
    )

    assert any("gemini-gone" in warning for warning in warnings)
    assert any("gemini-2.5-flash-image" in warning for warning in warnings)
    assert all("no longer" in warning.lower() or "deprecated" in warning.lower() for warning in warnings)


def test_option_label_marks_deprecated_models():
    assert option_label("gemini-old-image", live=["gemini-3.1-flash-image"]) == "gemini-old-image (deprecated)"
    assert option_label("gemini-old-image", live=None) == "gemini-old-image"


def test_option_label_includes_free_and_paid_pricing():
    lite = option_label("gemini-3.1-flash-lite", live=None)
    image = option_label("gemini-3.1-flash-image", live=["gemini-3.1-flash-image"])
    pro = option_label("gemini-3.1-pro-preview", live=None)
    deprecated_image = option_label("gemini-2.5-flash-image", live=["gemini-3.1-flash-image"])

    assert lite.startswith("gemini-3.1-flash-lite (")
    assert "free" in lite
    assert "$0.25/$1.50" in lite
    assert "paid ~$0.067/image" in image
    assert "paid $2.00/$12.00" in pro
    assert "deprecated" in deprecated_image
    assert "paid ~$0.039/image" in deprecated_image
    lite_pricing = pricing_for("gemini-3.1-flash-lite")
    image_lite_pricing = pricing_for("gemini-3.1-flash-lite-image")
    assert lite_pricing is not None and lite_pricing.free_tier is True
    assert image_lite_pricing is not None and image_lite_pricing.free_tier is False


def test_fetch_gemini_models_parses_and_paginates(monkeypatch):
    pages = [
        {
            "models": [
                {
                    "name": "models/gemini-3.1-flash-lite",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ],
            "nextPageToken": "page-2",
        },
        {
            "models": [
                {
                    "name": "models/gemini-3.1-flash-image",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ],
        },
    ]
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._payload

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    headers: list[str] = []

    def fake_urlopen(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        calls.append(url)
        headers.append(request.get_header("X-goog-api-key") or request.get_header("x-goog-api-key") or "")
        payload = pages[0] if "pageToken" not in url else pages[1]
        return FakeResponse(payload)

    text_models, image_models = fetch_gemini_models("secret-key", urlopen=fake_urlopen)

    assert text_models == ["gemini-3.1-flash-lite"]
    assert image_models == ["gemini-3.1-flash-image"]
    assert len(calls) == 2
    assert headers == ["secret-key", "secret-key"]
    assert "pageToken=page-2" in calls[1]


def test_refresh_persists_additive_union_and_updates_live_lists(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    save_catalog(
        path,
        ModelCatalog(
            text_models=["gemini-old-text"],
            image_models=["gemini-old-image"],
            live_text_models=["gemini-old-text"],
            live_image_models=["gemini-old-image"],
        ),
    )

    def fake_fetch(_api_key: str, **_kwargs):
        return ["gemini-3.1-flash-lite"], ["gemini-3.1-flash-image"]

    monkeypatch.setattr("model_catalog.fetch_gemini_models", fake_fetch)

    catalog = refresh_model_catalog(path, "secret-key")
    reloaded = load_catalog(path)

    assert "gemini-old-text" in catalog.text_models
    assert "gemini-3.1-flash-lite" in catalog.text_models
    assert "gemini-old-image" in catalog.image_models
    assert catalog.live_text_models == ["gemini-3.1-flash-lite"]
    assert catalog.live_image_models == ["gemini-3.1-flash-image"]
    assert reloaded.text_models == catalog.text_models
    assert reloaded.image_models == catalog.image_models
    assert reloaded.live_image_models == ["gemini-3.1-flash-image"]


def test_refresh_without_api_key_keeps_local_list_and_does_not_fetch(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    save_catalog(path, seed_catalog())

    def boom(_api_key: str, **_kwargs):
        raise AssertionError("should not fetch without an API key")

    monkeypatch.setattr("model_catalog.fetch_gemini_models", boom)

    catalog = refresh_model_catalog(path, api_key=None)

    assert catalog.live_text_models is None
    assert DEFAULT_MODEL in catalog.text_models
    assert DEFAULT_IMAGE_GENERATION_MODEL in catalog.image_models


def test_refresh_fetch_failure_leaves_existing_catalog_intact(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    original = ModelCatalog(
        text_models=["gemini-3.1-flash-lite"],
        image_models=["gemini-2.5-flash-image"],
        live_text_models=["gemini-3.1-flash-lite"],
        live_image_models=["gemini-2.5-flash-image"],
    )
    save_catalog(path, original)

    def fake_fetch(_api_key: str, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr("model_catalog.fetch_gemini_models", fake_fetch)

    catalog = refresh_model_catalog(path, "secret-key")
    reloaded = load_catalog(path)

    assert catalog.fetch_error is not None
    assert "network down" in catalog.fetch_error
    assert catalog.text_models == original.text_models
    assert catalog.image_models == original.image_models
    assert catalog.live_image_models == original.live_image_models
    assert reloaded.live_image_models == original.live_image_models


def test_load_missing_catalog_uses_seed(tmp_path):
    catalog = load_catalog(tmp_path / "missing.json")

    assert catalog.text_models == seed_catalog().text_models
    assert catalog.image_models == seed_catalog().image_models


def test_input_image_cap_uses_character_resemblance_limits() -> None:
    assert input_image_cap("gemini-2.5-flash-image") == 3
    assert input_image_cap("gemini-3.1-flash-lite-image") == 4
    assert input_image_cap("gemini-3.1-flash-image") == 4
    assert input_image_cap("gemini-3-pro-image") == 5
    assert input_image_cap("gemini-3.1-flash-image-preview") == 4
    assert input_image_cap("gemini-unknown-image") == 3
    assert input_image_cap("gemini-test") == 3
