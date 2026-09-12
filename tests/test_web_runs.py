from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from pipeline_events import PhaseCompleted, PhaseStarted, RunCompleted
from run_controller import RunController
from web.app import create_app


class _CompletesPipeline:
    def __init__(self, event_callback, **kwargs: object) -> None:
        self._event_callback = event_callback
        self.kwargs = kwargs

    async def run(self) -> dict[str, object]:
        self._event_callback(PhaseStarted(phase="script", message="Writing script..."))
        self._event_callback(PhaseCompleted(phase="script", message="...done"))
        self._event_callback(
            RunCompleted(
                status="ok",
                version="v001",
                version_dir="/secret/campaigns/dreadmarsh/crossing/v001",
                checkpoints=["script"],
                failed_phases=[],
                error_messages=[],
            )
        )
        return {
            "version": "v001",
            "version_dir": "/secret/campaigns/dreadmarsh/crossing/v001",
            "errors": [],
        }


class _BlocksUntilCancelledPipeline:
    def __init__(self, event_callback, **kwargs: object) -> None:
        self._event_callback = event_callback

    async def run(self) -> dict[str, object]:
        self._event_callback(PhaseStarted(phase="script", message="Writing script..."))
        await asyncio.Event().wait()
        return {}


async def _async_client(tmp_path: Path, pipeline_factory):
    from httpx import ASGITransport, AsyncClient

    app = create_app(
        campaigns_root=tmp_path / "campaigns",
        settings_path=tmp_path / "settings.json",
        run_controller=RunController(pipeline_factory=pipeline_factory),
    )
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), app


async def _wait_for_run(client, run_id: str) -> dict:
    for _ in range(100):
        response = await client.get(f"/api/runs/{run_id}")
        payload = response.json()
        if payload["status"] != "running":
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} stayed running")


@pytest.mark.asyncio
async def test_launch_run_records_events_and_forwards_config(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def factory(**kwargs: object) -> _CompletesPipeline:
        captured.update(kwargs)
        return _CompletesPipeline(**kwargs)

    client, _app = await _async_client(tmp_path, factory)
    async with client:
        created = await client.post(
            "/api/runs",
            json={
                "url": "https://example.test/story",
                "campaign": "dreadmarsh",
                "rerun_from": "architect",
                "stop_after": "script",
                "art_style": "bundled:brutalist",
                "vignette": True,
                "panel_count": 4,
            },
        )
        assert created.status_code == 202
        run_id = created.json()["id"]
        assert created.json()["status"] == "running"

        snapshot = await _wait_for_run(client, run_id)
        assert snapshot["status"] == "ok"
        assert snapshot["version"] == "v001"
        assert [event["type"] for event in snapshot["events"]] == [
            "PhaseStarted",
            "PhaseCompleted",
            "RunCompleted",
        ]
        assert snapshot["events"][0]["phase"] == "script"
        assert snapshot["events"][-1]["status"] == "ok"

        current = await client.get("/api/runs/current")
        assert current.json()["status"] == "idle"

        sse = await client.get(f"/api/runs/{run_id}/events")
        assert sse.status_code == 200
        assert "PhaseStarted" in sse.text
        assert "RunCompleted" in sse.text

    assert captured["campaigns_root"] == tmp_path / "campaigns"
    assert captured["url"] == "https://example.test/story"
    assert captured["campaign"] == "dreadmarsh"
    assert captured["rerun_from"] == "architect"
    assert captured["stop_after"] == "script"
    assert captured["art_style"] == "bundled:brutalist"
    assert captured["vignette"] is True
    assert captured["panel_count"] == 4


@pytest.mark.asyncio
async def test_second_run_conflicts_until_cancelled(tmp_path: Path) -> None:
    client, _app = await _async_client(tmp_path, _BlocksUntilCancelledPipeline)
    async with client:
        first = await client.post(
            "/api/runs",
            json={"url": "https://example.test/story", "campaign": "dreadmarsh"},
        )
        assert first.status_code == 202
        run_id = first.json()["id"]

        current = None
        for _ in range(100):
            current = (await client.get("/api/runs/current")).json()
            if current.get("phase") == "script":
                break
            await asyncio.sleep(0.01)
        assert current is not None
        assert current["id"] == run_id
        assert current["status"] == "running"
        assert current["phase"] == "script"

        second = await client.post(
            "/api/runs",
            json={"url": "https://example.test/other", "campaign": "dreadmarsh"},
        )
        assert second.status_code == 409

        cancelled = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        snapshot = await _wait_for_run(client, run_id)
        assert snapshot["status"] == "cancelled"

        current = await client.get("/api/runs/current")
        assert current.json()["status"] == "idle"


@pytest.mark.asyncio
async def test_launch_run_rejects_empty_url(tmp_path: Path) -> None:
    client, _app = await _async_client(tmp_path, _CompletesPipeline)
    async with client:
        response = await client.post(
            "/api/runs",
            json={"url": "  ", "campaign": "dreadmarsh"},
        )
        assert response.status_code == 400
