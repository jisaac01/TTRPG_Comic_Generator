from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from web.app import create_app


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
