from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from art_styles import default_art_direction_template_path
from model_defaults import DEFAULT_IMAGE_GENERATION_MODEL, DEFAULT_MODEL
from prompt_templates import PAGE_PROMPT_TEMPLATE_FILENAME, DEFAULT_PROMPTS_DIR
from prompter import ART_DIRECTION_TEMPLATE_FIELDS
from web.app import create_app

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _client(tmp_path: Path):
    from fastapi.testclient import TestClient

    app = create_app(
        campaigns_root=tmp_path / "campaigns",
        settings_path=tmp_path / "settings.json",
    )
    return TestClient(app)


def test_health_reports_ready_and_preflight_warnings(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["warnings"], list)
    assert all(isinstance(item, str) for item in payload["warnings"])


def test_landing_page_identifies_the_app(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "TTRPG Comic Generator" in response.text
    assert 'data-workspace="run"' in response.text
    assert 'data-workspace="prompts"' in response.text
    assert 'data-workspace="output"' in response.text


def test_static_ui_assets_are_served(tmp_path: Path) -> None:
    client = _client(tmp_path)

    script = client.get("/static/app.js")
    styles = client.get("/static/app.css")

    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert styles.status_code == 200
    assert "css" in styles.headers["content-type"]


def test_list_campaigns_empty_when_root_missing(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/campaigns")

    assert response.status_code == 200
    assert response.json() == {"campaigns": []}


def test_list_campaigns_returns_existing_directories(tmp_path: Path) -> None:
    campaigns_root = tmp_path / "campaigns"
    (campaigns_root / "flail").mkdir(parents=True)
    (campaigns_root / "dreadmarsh").mkdir()
    client = _client(tmp_path)

    response = client.get("/api/campaigns")

    assert response.status_code == 200
    assert response.json() == {"campaigns": ["dreadmarsh", "flail"]}


def test_create_campaign_writes_directory_and_lists_it(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post("/api/campaigns", json={"name": "dreadmarsh"})

    assert created.status_code == 201
    assert created.json() == {"name": "dreadmarsh"}
    assert (tmp_path / "campaigns" / "dreadmarsh").is_dir()

    listed = client.get("/api/campaigns")

    assert listed.status_code == 200
    assert listed.json() == {"campaigns": ["dreadmarsh"]}


def test_create_campaign_conflict_when_name_exists(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = client.post("/api/campaigns", json={"name": "dreadmarsh"})
    assert first.status_code == 201

    duplicate = client.post("/api/campaigns", json={"name": "dreadmarsh"})

    assert duplicate.status_code == 409
    assert (tmp_path / "campaigns" / "dreadmarsh").is_dir()
    listed = client.get("/api/campaigns")
    assert listed.json() == {"campaigns": ["dreadmarsh"]}


def test_create_campaign_rejects_empty_or_path_name(tmp_path: Path) -> None:
    client = _client(tmp_path)

    empty = client.post("/api/campaigns", json={"name": "   "})
    nested = client.post("/api/campaigns", json={"name": "foo/bar"})

    assert empty.status_code == 400
    assert nested.status_code == 400
    assert client.get("/api/campaigns").json() == {"campaigns": []}


def _seed_episode(campaigns_root: Path) -> Path:
    episode = campaigns_root / "dreadmarsh" / "crossing"
    episode.mkdir(parents=True)
    (episode / "episode_meta.json").write_text(
        json.dumps(
            {
                "url": "https://example.test/story",
                "slug": "crossing",
                "title": "Crossing",
                "created_at": "2026-05-04T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    v001 = episode / "v001"
    v001.mkdir()
    (v001 / "01_raw_text.json").write_text('{"title":"Crossing"}', encoding="utf-8")
    (v001 / "04_page_1_prompt.txt").write_text("draw the marsh\n", encoding="utf-8")
    (v001 / "run_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "version": "v001",
                "version_dir": "/secret/campaigns/dreadmarsh/crossing/v001",
                "checkpoints": ["raw_text", "prompt"],
                "failed": [],
                "errors": [],
                "starred": True,
                "description": "first pass",
                "run_config": {
                    "panel_count": 6,
                    "total_pages": 1,
                    "campaigns_root": "/secret/campaigns",
                    "art_style": "bundled:brutalist",
                },
            }
        ),
        encoding="utf-8",
    )
    image_path = v001 / "images" / "run1" / "05_page_1.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_TINY_PNG)
    working = episode / "working"
    working.mkdir()
    (working / "01_raw_text.json").write_text('{"title":"Crossing"}', encoding="utf-8")
    return episode


def test_list_episodes_includes_title_and_image_flag(tmp_path: Path) -> None:
    campaigns_root = tmp_path / "campaigns"
    _seed_episode(campaigns_root)
    client = _client(tmp_path)

    response = client.get("/api/campaigns/dreadmarsh/episodes")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "episodes": [
            {
                "slug": "crossing",
                "title": "Crossing",
                "url": "https://example.test/story",
                "created_at": "2026-05-04T00:00:00+00:00",
                "has_images": True,
            }
        ]
    }


def test_list_art_styles_includes_bundled_and_campaign(tmp_path: Path) -> None:
    campaigns_root = tmp_path / "campaigns"
    art_dir = campaigns_root / "dreadmarsh" / "art_direction"
    art_dir.mkdir(parents=True)
    (art_dir / "swamp-ink.json").write_text('{"base_style": "swamp"}', encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/api/campaigns/dreadmarsh/art-styles")

    assert response.status_code == 200
    styles = response.json()["styles"]
    by_id = {item["id"]: item for item in styles}
    assert by_id["bundled:brutalist"]["label"] == "brutalist (bundled)"
    assert by_id["bundled:brutalist"]["source"] == "bundled"
    assert by_id["campaign:swamp-ink"] == {
        "id": "campaign:swamp-ink",
        "stem": "swamp-ink",
        "label": "swamp-ink (campaign)",
        "source": "campaign",
    }


def test_list_and_get_campaign_prompts(tmp_path: Path) -> None:
    campaigns_root = tmp_path / "campaigns"
    campaign = campaigns_root / "dreadmarsh"
    campaign.mkdir(parents=True)
    (campaign / "page_prompt.txt").write_text("campaign page prompt\n", encoding="utf-8")
    client = _client(tmp_path)

    listed = client.get("/api/campaigns/dreadmarsh/prompts")
    assert listed.status_code == 200
    keys = [item["key"] for item in listed.json()["prompts"]]
    assert "bundled:brutalist" in keys
    assert "page_prompt" in keys
    page_prompt = next(item for item in listed.json()["prompts"] if item["key"] == "page_prompt")
    assert page_prompt["exists"] is True
    assert page_prompt["label"] == "page_prompt.txt"

    loaded = client.get("/api/campaigns/dreadmarsh/prompts/page_prompt")
    assert loaded.status_code == 200
    assert loaded.json()["content"] == "campaign page prompt\n"

    bundled = client.get("/api/campaigns/dreadmarsh/prompts/bundled:brutalist")
    assert bundled.status_code == 200
    assert bundled.json()["content"] == default_art_direction_template_path().read_text(
        encoding="utf-8"
    )


def test_get_prompt_falls_back_to_default_template(tmp_path: Path) -> None:
    (tmp_path / "campaigns" / "dreadmarsh").mkdir(parents=True)
    client = _client(tmp_path)

    response = client.get("/api/campaigns/dreadmarsh/prompts/page_prompt")

    assert response.status_code == 200
    assert (
        response.json()["content"]
        == (DEFAULT_PROMPTS_DIR / PAGE_PROMPT_TEMPLATE_FILENAME).read_text(encoding="utf-8")
    )


def test_list_versions_puts_working_first_and_omits_disk_paths(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    response = client.get("/api/campaigns/dreadmarsh/episodes/crossing/versions")

    assert response.status_code == 200
    versions = response.json()["versions"]
    assert [item["version"] for item in versions] == ["working", "v001"]
    working, historical = versions
    assert working["editable"] is True
    assert working["has_images"] is False
    assert historical == {
        "version": "v001",
        "label": "★ v001",
        "status": "ok",
        "starred": True,
        "description": "first pass",
        "has_images": True,
        "editable": False,
    }


def test_version_status_returns_run_settings(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    response = client.get(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/status"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checkpoints"] == ["raw_text", "prompt"]
    assert payload["starred"] is True
    assert payload["description"] == "first pass"
    assert payload["run_config"]["panel_count"] == 6
    assert payload["run_config"]["art_style"] == "bundled:brutalist"


def test_list_and_get_version_files_pretty_prints_json(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    listed = client.get(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/files"
    )
    assert listed.status_code == 200
    keys = [item["key"] for item in listed.json()["files"]]
    assert keys[:1] == ["01_raw_text.json"]
    assert "04_page_1_prompt.txt" in keys
    assert "images/run1/05_page_1.png" in keys
    by_key = {item["key"]: item for item in listed.json()["files"]}
    assert by_key["images/run1/05_page_1.png"]["kind"] == "image"
    assert by_key["01_raw_text.json"]["kind"] == "text"

    raw = client.get(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/files/01_raw_text.json"
    )
    assert raw.status_code == 200
    assert raw.json()["content"] == '{\n  "title": "Crossing"\n}'

    prompt = client.get(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/files/04_page_1_prompt.txt"
    )
    assert prompt.status_code == 200
    assert prompt.json()["content"] == "draw the marsh\n"


def test_working_file_list_includes_creative_direction_placeholder(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    response = client.get(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/working/files"
    )

    assert response.status_code == 200
    files = response.json()["files"]
    assert files[0] == {
        "key": "creative_direction.txt",
        "kind": "text",
        "exists": False,
    }


def test_media_endpoint_returns_image_bytes(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    response = client.get(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/media/images/run1/05_page_1.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == _TINY_PNG


def test_file_key_traversal_is_rejected(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    response = client.get(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/files/%2e%2e/episode_meta.json"
    )

    assert response.status_code == 400


def test_save_and_reset_campaign_prompt(tmp_path: Path) -> None:
    campaigns_root = tmp_path / "campaigns"
    campaign = campaigns_root / "dreadmarsh"
    campaign.mkdir(parents=True)
    (campaign / "page_prompt.txt").write_text("custom prompt\n", encoding="utf-8")
    default = (DEFAULT_PROMPTS_DIR / PAGE_PROMPT_TEMPLATE_FILENAME).read_text(
        encoding="utf-8"
    )
    client = _client(tmp_path)

    saved = client.put(
        "/api/campaigns/dreadmarsh/prompts/page_prompt",
        json={"content": "edited prompt\n"},
    )
    assert saved.status_code == 200
    assert saved.json()["key"] == "page_prompt"
    assert saved.json()["content"] == "edited prompt\n"
    assert (campaign / "page_prompt.txt").read_text(encoding="utf-8") == "edited prompt\n"

    reset = client.post("/api/campaigns/dreadmarsh/prompts/page_prompt/reset")
    assert reset.status_code == 200
    assert reset.json()["content"] == default
    assert (campaign / "page_prompt.txt").read_text(encoding="utf-8") == "edited prompt\n"

    loaded = client.get("/api/campaigns/dreadmarsh/prompts/page_prompt")
    assert loaded.json()["content"] == "edited prompt\n"


def test_save_bundled_art_style_writes_campaign_override(tmp_path: Path) -> None:
    (tmp_path / "campaigns" / "dreadmarsh").mkdir(parents=True)
    payload = {name: f"override {name}" for name, _ in ART_DIRECTION_TEMPLATE_FIELDS}
    client = _client(tmp_path)

    saved = client.put(
        "/api/campaigns/dreadmarsh/prompts/bundled:brutalist",
        json={"content": json.dumps(payload, indent=2)},
    )

    assert saved.status_code == 200
    assert saved.json()["key"] == "campaign:brutalist"
    saved_path = (
        tmp_path / "campaigns" / "dreadmarsh" / "art_direction" / "brutalist.json"
    )
    assert json.loads(saved_path.read_text(encoding="utf-8")) == payload
    bundled = client.get("/api/campaigns/dreadmarsh/prompts/bundled:brutalist")
    assert bundled.status_code == 200
    assert bundled.json()["content"] == default_art_direction_template_path().read_text(
        encoding="utf-8"
    )


def test_save_invalid_art_style_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "campaigns" / "dreadmarsh").mkdir(parents=True)
    client = _client(tmp_path)

    response = client.put(
        "/api/campaigns/dreadmarsh/prompts/campaign:swamp-ink",
        json={"content": "{}"},
    )

    assert response.status_code == 400
    assert "at least one" in response.json()["detail"].lower()


def test_save_working_file_and_reject_historical_write(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    saved = client.put(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/working/files/creative_direction.txt",
        json={"content": "focus on the witch"},
    )
    assert saved.status_code == 200
    assert saved.json()["content"] == "focus on the witch\n"
    working_path = (
        tmp_path
        / "campaigns"
        / "dreadmarsh"
        / "crossing"
        / "working"
        / "creative_direction.txt"
    )
    assert working_path.read_text(encoding="utf-8") == "focus on the witch\n"

    historical = client.put(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/files/01_raw_text.json",
        json={"content": '{"title":"nope"}'},
    )
    assert historical.status_code == 400
    raw = (
        tmp_path
        / "campaigns"
        / "dreadmarsh"
        / "crossing"
        / "v001"
        / "01_raw_text.json"
    )
    assert raw.read_text(encoding="utf-8") == '{"title":"Crossing"}'


def test_star_and_note_historical_version(tmp_path: Path) -> None:
    _seed_episode(tmp_path / "campaigns")
    client = _client(tmp_path)

    updated = client.patch(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001",
        json={"starred": False, "description": "  keep the fog  "},
    )
    assert updated.status_code == 200
    assert updated.json()["starred"] is False
    assert updated.json()["description"] == "keep the fog"
    assert updated.json()["version"] == "v001"

    listed = client.get("/api/campaigns/dreadmarsh/episodes/crossing/versions")
    historical = next(item for item in listed.json()["versions"] if item["version"] == "v001")
    assert historical["starred"] is False
    assert historical["description"] == "keep the fog"

    working = client.patch(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/working",
        json={"starred": True},
    )
    assert working.status_code == 400


def test_settings_round_trip_masks_api_key(tmp_path: Path, monkeypatch) -> None:
    import os

    import settings_service

    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        settings_service.keyring,
        "get_password",
        lambda service, user: stored.get((service, user)),
    )
    monkeypatch.setattr(
        settings_service.keyring,
        "set_password",
        lambda service, user, password: stored.__setitem__((service, user), password),
    )
    client = _client(tmp_path)

    before = client.get("/api/settings")
    assert before.status_code == 200
    assert before.json()["gemini_api_key_configured"] is False
    assert before.json()["default_model"] == DEFAULT_MODEL
    assert before.json()["image_generation_model"] == DEFAULT_IMAGE_GENERATION_MODEL
    assert DEFAULT_MODEL in before.json()["text_models"]

    saved = client.put(
        "/api/settings",
        json={
            "gemini_api_key": "secret-key",
            "default_model": "gemini-3.1-pro",
            "image_generation_model": "gemini-3.1-flash-image",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["gemini_api_key_configured"] is True
    assert saved.json()["gemini_api_key_masked"].endswith("-key")
    assert saved.json()["default_model"] == "gemini-3.1-pro"
    assert os.environ["GEMINI_API_KEY"] == "secret-key"

    loaded = client.get("/api/settings")
    assert loaded.json()["default_model"] == "gemini-3.1-pro"
    assert loaded.json()["image_generation_model"] == "gemini-3.1-flash-image"
    assert loaded.json()["gemini_api_key_configured"] is True


def test_refresh_models_uses_mocked_gemini_list(tmp_path: Path, monkeypatch) -> None:
    import model_catalog
    import settings_service

    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        settings_service.keyring,
        "get_password",
        lambda service, user: stored.get((service, user)),
    )
    monkeypatch.setattr(
        settings_service.keyring,
        "set_password",
        lambda service, user, password: stored.__setitem__((service, user), password),
    )
    monkeypatch.setattr(
        model_catalog,
        "fetch_gemini_models",
        lambda api_key, **_kwargs: (["gemini-test-text"], ["gemini-test-image"]),
    )
    client = _client(tmp_path)
    client.put("/api/settings", json={"gemini_api_key": "secret-key"})

    refreshed = client.post("/api/settings/refresh-models")

    assert refreshed.status_code == 200
    assert "gemini-test-text" in refreshed.json()["text_models"]
    assert "gemini-test-image" in refreshed.json()["image_models"]
    catalog = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert "gemini-test-text" in catalog["text_models"]

