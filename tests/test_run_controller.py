from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from pipeline_config import RunConfig
from pipeline_events import PhaseCompleted, PhaseStarted, RunCompleted, VersionCreated
from repository_service import RepositoryService
from run_controller import RunController


class _EmitsAndCompletesPipeline:
    def __init__(self, event_callback, **_: object) -> None:
        self._event_callback = event_callback

    async def run(self) -> dict[str, object]:
        self._event_callback(PhaseStarted(phase="script", message="Writing script..."))
        self._event_callback(PhaseCompleted(phase="script", message="...done"))
        self._event_callback(
            RunCompleted(
                status="ok",
                version="v001",
                version_dir="/tmp/v001",
                checkpoints=["script"],
                failed_phases=[],
                error_messages=[],
            )
        )
        return {
            "version": "v001",
            "version_dir": "/tmp/v001",
            "errors": [],
        }


class _BlocksUntilCancelledPipeline:
    def __init__(self, event_callback, **_: object) -> None:
        self._event_callback = event_callback

    async def run(self) -> dict[str, object]:
        self._event_callback(PhaseStarted(phase="script", message="Writing script..."))
        await asyncio.Event().wait()
        return {}


class _WritesVersionPipeline:
    def __init__(self, *, campaigns_root: Path, campaign: str, url: str, event_callback, **_: object) -> None:
        self._campaigns_root = campaigns_root
        self._campaign = campaign
        self._url = url
        self._event_callback = event_callback

    async def run(self) -> dict[str, object]:
        episode_slug = "controller-integration-episode"
        episode_dir = self._campaigns_root / self._campaign / episode_slug
        version_dir = episode_dir / "v001"
        version_dir.mkdir(parents=True, exist_ok=True)

        (episode_dir / "episode_meta.json").write_text(
            json.dumps(
                {
                    "url": self._url,
                    "slug": episode_slug,
                    "title": "Controller Integration Episode",
                    "created_at": "2026-05-18T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        (version_dir / "run_status.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "checkpoints": ["raw_text", "entities", "script"],
                    "failed": [],
                    "errors": [],
                }
            ),
            encoding="utf-8",
        )

        self._event_callback(
            VersionCreated(
                version="v001",
                version_dir=str(version_dir),
                episode_slug=episode_slug,
            )
        )
        self._event_callback(
            RunCompleted(
                status="ok",
                version="v001",
                version_dir=str(version_dir),
                checkpoints=["raw_text", "entities", "script"],
                failed_phases=[],
                error_messages=[],
            )
        )

        return {
            "version": "v001",
            "version_dir": str(version_dir),
            "errors": [],
        }


class _WritesVersionThenFailsPipeline:
    def __init__(self, *, campaigns_root: Path, campaign: str, url: str, event_callback, **_: object) -> None:
        self._campaigns_root = campaigns_root
        self._campaign = campaign
        self._url = url
        self._event_callback = event_callback

    async def run(self) -> dict[str, object]:
        episode_slug = "controller-failure-episode"
        episode_dir = self._campaigns_root / self._campaign / episode_slug
        version_dir = episode_dir / "v001"
        version_dir.mkdir(parents=True, exist_ok=True)

        (episode_dir / "episode_meta.json").write_text(
            json.dumps(
                {
                    "url": self._url,
                    "slug": episode_slug,
                    "title": "Controller Failure Episode",
                    "created_at": "2026-05-18T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        self._event_callback(
            VersionCreated(
                version="v001",
                version_dir=str(version_dir),
                episode_slug=episode_slug,
            )
        )

        raise RuntimeError("network timeout")


class _WritesVersionThenBlocksPipeline:
    def __init__(self, *, campaigns_root: Path, campaign: str, url: str, event_callback, **_: object) -> None:
        self._campaigns_root = campaigns_root
        self._campaign = campaign
        self._url = url
        self._event_callback = event_callback

    async def run(self) -> dict[str, object]:
        episode_slug = "controller-cancelled-episode"
        episode_dir = self._campaigns_root / self._campaign / episode_slug
        version_dir = episode_dir / "v001"
        version_dir.mkdir(parents=True, exist_ok=True)

        (episode_dir / "episode_meta.json").write_text(
            json.dumps(
                {
                    "url": self._url,
                    "slug": episode_slug,
                    "title": "Controller Cancelled Episode",
                    "created_at": "2026-05-18T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        self._event_callback(
            VersionCreated(
                version="v001",
                version_dir=str(version_dir),
                episode_slug=episode_slug,
            )
        )

        await asyncio.Event().wait()
        return {}


@pytest.mark.asyncio
async def test_run_controller_launches_and_emits_events_in_order(tmp_path):
    controller = RunController(pipeline_factory=_EmitsAndCompletesPipeline)
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
    )

    received_event_types: list[str] = []
    task = controller.launch_run(config, lambda event: received_event_types.append(type(event).__name__))
    result = await task

    assert received_event_types == ["PhaseStarted", "PhaseCompleted", "RunCompleted"]
    assert result.status == "ok"
    assert result.version == "v001"
    assert result.version_dir == "/tmp/v001"
    assert result.errors == []
    assert result.error_details == []
    assert controller.current_run() is None
    assert controller.last_result() is not None


@pytest.mark.asyncio
async def test_run_controller_cancel_stops_active_run(tmp_path):
    controller = RunController(pipeline_factory=_BlocksUntilCancelledPipeline)
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
    )

    seen_event_types: list[str] = []
    task = controller.launch_run(config, lambda event: seen_event_types.append(type(event).__name__))

    await asyncio.sleep(0)
    cancelled = await controller.cancel_run()
    result = await task

    assert cancelled is True
    assert result.status == "cancelled"
    assert result.error_details == ["Run cancelled by user."]
    assert seen_event_types == ["PhaseStarted"]
    assert controller.current_run() is None


@pytest.mark.asyncio
async def test_run_controller_integration_repository_service_discovers_created_version(tmp_path):
    controller = RunController(pipeline_factory=_WritesVersionPipeline)
    repository = RepositoryService(tmp_path)
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
    )

    task = controller.launch_run(config, lambda _event: None)
    result = await task

    assert result.status == "ok"
    assert result.version == "v001"
    assert result.version_dir is not None

    episodes = repository.list_episodes("dreadmarsh")
    assert [episode.slug for episode in episodes] == ["controller-integration-episode"]
    assert repository.latest_version("dreadmarsh", "controller-integration-episode") == "v001"

    versions = repository.list_versions("dreadmarsh", "controller-integration-episode")
    assert len(versions) == 1
    assert versions[0].status == "ok"
    assert versions[0].version == "v001"


@pytest.mark.asyncio
async def test_run_controller_persists_run_status_on_failure(tmp_path):
    controller = RunController(pipeline_factory=_WritesVersionThenFailsPipeline)
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
    )

    task = controller.launch_run(config, lambda _event: None)
    result = await task

    assert result.status == "failed"
    assert result.version_dir is not None

    status_path = Path(result.version_dir) / "run_status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["campaign"] == "dreadmarsh"
    assert status["errors"] == ["network timeout"]
    assert len(status["error_details"]) == 1
    assert "RuntimeError: network timeout" in status["error_details"][0]


@pytest.mark.asyncio
async def test_run_controller_persists_run_status_on_cancel(tmp_path):
    controller = RunController(pipeline_factory=_WritesVersionThenBlocksPipeline)
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
    )

    task = controller.launch_run(config, lambda _event: None)
    await asyncio.sleep(0)
    cancelled = await controller.cancel_run()
    result = await task

    assert cancelled is True
    assert result.status == "cancelled"
    assert result.version_dir is not None

    status_path = Path(result.version_dir) / "run_status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "cancelled"
    assert status["campaign"] == "dreadmarsh"
    assert status["errors"] == ["Run cancelled by user."]
    assert status["error_details"] == ["Run cancelled by user."]