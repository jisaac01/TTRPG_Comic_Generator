from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from web.app import create_app

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _client(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        "web.api.images.ImageGenerator.generate_image",
        lambda self, prompt, reference_images=None: _TINY_PNG,
    )
    app = create_app(
        campaigns_root=tmp_path / "campaigns",
        settings_path=tmp_path / "settings.json",
    )
    return TestClient(app)


def _seed_version(campaigns_root: Path, *, panel_prompts: bool = False) -> Path:
    episode = campaigns_root / "dreadmarsh" / "crossing"
    v001 = episode / "v001"
    v001.mkdir(parents=True)
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
    if panel_prompts:
        (v001 / "04_page_1_panel_1_prompt.txt").write_text("panel one\n", encoding="utf-8")
        (v001 / "04_page_1_panel_2_prompt.txt").write_text("panel two\n", encoding="utf-8")
        generation_mode = "panel"
    else:
        (v001 / "04_page_1_prompt.txt").write_text("draw the marsh\n", encoding="utf-8")
        generation_mode = "page"
    (v001 / "run_status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "version": "v001",
                "checkpoints": ["prompt"],
                "failed": [],
                "errors": [],
                "run_config": {
                    "aspect_ratio": "3:2",
                    "generation_mode": generation_mode,
                },
            }
        ),
        encoding="utf-8",
    )
    return v001


def test_generate_all_writes_images_and_records_the_job(tmp_path: Path, monkeypatch) -> None:
    version_dir = _seed_version(tmp_path / "campaigns")
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/images/generate"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "generate_all"
    assert payload["images_dir"] == "images/v001"
    assert payload["files"] == ["images/v001/05_page_1.png"]
    image_path = version_dir / "images" / "v001" / "05_page_1.png"
    assert image_path.read_bytes() == _TINY_PNG
    status = json.loads((version_dir / "run_status.json").read_text(encoding="utf-8"))
    record = status["image_generations"][-1]
    assert record["source"] == "generate_all"
    assert record["files"] == ["05_page_1.png"]
    assert record["images_dir"] == "images/v001"


def test_generate_selected_and_test_image_use_latest_folder(tmp_path: Path, monkeypatch) -> None:
    version_dir = _seed_version(tmp_path / "campaigns")
    client = _client(tmp_path, monkeypatch)
    client.post(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/images/generate"
    )

    selected = client.post(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/images/generate-selected",
        json={"prompt": "04_page_1_prompt.txt"},
    )
    assert selected.status_code == 200
    assert selected.json()["source"] == "regenerate_selected"
    assert selected.json()["files"] == ["images/v001/05_page_1_v1.png"]
    assert (version_dir / "images" / "v001" / "05_page_1_v1.png").is_file()

    tested = client.post(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/images/test"
    )
    assert tested.status_code == 200
    assert tested.json()["source"] == "test_image"
    assert tested.json()["files"] == ["images/v001/05_page_1_v2.png"]
    status = json.loads((version_dir / "run_status.json").read_text(encoding="utf-8"))
    sources = [item["source"] for item in status["image_generations"]]
    assert sources == ["generate_all", "regenerate_selected", "test_image"]


def test_stitch_builds_a_page_from_panel_images(tmp_path: Path, monkeypatch) -> None:
    version_dir = _seed_version(tmp_path / "campaigns", panel_prompts=True)
    images_dir = version_dir / "images" / "v001"
    images_dir.mkdir(parents=True)
    (images_dir / "05_page_1_panel_1.png").write_bytes(_TINY_PNG)
    (images_dir / "05_page_1_panel_2.png").write_bytes(_TINY_PNG)
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/images/stitch"
    )

    assert response.status_code == 200
    assert response.json()["source"] == "stitch"
    assert response.json()["stitched"] == ["images/v001/06_page_1.png"]
    assert (images_dir / "06_page_1.png").is_file()


def test_generate_all_panel_mode_stitches_pages(tmp_path: Path, monkeypatch) -> None:
    version_dir = _seed_version(tmp_path / "campaigns", panel_prompts=True)
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/images/generate"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["files"] == [
        "images/v001/05_page_1_panel_1.png",
        "images/v001/05_page_1_panel_2.png",
    ]
    assert payload["stitched"] == ["images/v001/06_page_1.png"]
    assert (version_dir / "images" / "v001" / "06_page_1.png").is_file()


def test_generate_selected_requires_a_page_prompt(tmp_path: Path, monkeypatch) -> None:
    version_dir = _seed_version(tmp_path / "campaigns")
    (version_dir / "01_raw_text.json").write_text("{}", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/campaigns/dreadmarsh/episodes/crossing/versions/v001/images/generate-selected",
        json={"prompt": "01_raw_text.json"},
    )

    assert response.status_code == 400
