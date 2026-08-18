from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from repository_service import RepositoryService


def _write_version(version_dir: Path, status: str = "ok") -> None:
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "01_raw_text.json").write_text("{}", encoding="utf-8")
    (version_dir / "02_entities.json").write_text("{}", encoding="utf-8")
    (version_dir / "02_5_story_bible.txt").write_text("Scene 1:\nTest scene.\n", encoding="utf-8")
    (version_dir / "03_script.json").write_text("{}", encoding="utf-8")
    (version_dir / "03_5_styled_script.json").write_text("{}", encoding="utf-8")
    (version_dir / "04_page_1_prompt.txt").write_text("prompt", encoding="utf-8")
    (version_dir / "art_direction_template.json").write_text("{}", encoding="utf-8")
    prompts_dir = version_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    for filename in (
        "story_architect_system.txt",
        "story_architect_user.txt",
        "scriptwriter_system.txt",
        "scriptwriter_user.txt",
        "style_integrator_system.txt",
        "style_integrator_user.txt",
        "page_prompt.txt",
    ):
        (prompts_dir / filename).write_text(filename, encoding="utf-8")
    (version_dir / "run_status.json").write_text(
        json.dumps(
            {
                "status": status,
                "checkpoints": ["raw_text", "entities", "script"],
                "failed": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )


def test_repository_service_discovers_campaigns_episodes_versions_and_prompts(tmp_path):
    campaigns_root = tmp_path / "campaigns"
    campaign_root = campaigns_root / "dreadmarsh"
    campaign_root.mkdir(parents=True)
    art_dir = campaign_root / "art_direction"
    art_dir.mkdir()
    (art_dir / "brutalist.json").write_text("{}", encoding="utf-8")
    for filename in (
        "story_architect_system.txt",
        "story_architect_user.txt",
        "scriptwriter_system.txt",
        "scriptwriter_user.txt",
        "style_integrator_system.txt",
        "style_integrator_user.txt",
        "page_prompt.txt",
    ):
        (campaign_root / filename).write_text(filename, encoding="utf-8")

    episode_dir = campaign_root / "dreadmarsh-crossing"
    episode_dir.mkdir()
    (episode_dir / "episode_meta.json").write_text(
        json.dumps(
            {
                "url": "https://example.test/story",
                "slug": "dreadmarsh-crossing",
                "title": "Dreadmarsh Crossing",
                "created_at": "2026-05-04T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    _write_version(episode_dir / "v001", status="ok")
    _write_version(episode_dir / "v002", status="partial")

    service = RepositoryService(campaigns_root)

    assert service.list_campaigns() == ["dreadmarsh"]

    episodes = service.list_episodes("dreadmarsh")
    assert len(episodes) == 1
    assert episodes[0].slug == "dreadmarsh-crossing"
    assert episodes[0].title == "Dreadmarsh Crossing"

    assert service.latest_version("dreadmarsh", "dreadmarsh-crossing") == "v002"

    versions = service.list_versions("dreadmarsh", "dreadmarsh-crossing")
    assert [version.version for version in versions] == ["v001", "v002"]
    assert versions[0].status == "ok"
    assert versions[1].status == "partial"
    assert versions[0].starred is False
    assert versions[0].description == ""
    assert versions[1].starred is False
    assert versions[1].description == ""

    files = service.get_version_files("dreadmarsh", "dreadmarsh-crossing", "v002")
    assert files.raw_text == episode_dir / "v002" / "01_raw_text.json"
    assert files.page_prompt == episode_dir / "v002" / "04_page_1_prompt.txt"
    assert files.prompts_dir == episode_dir / "v002" / "prompts"

    campaign_prompts = service.get_campaign_prompts("dreadmarsh")
    assert campaign_prompts.page_prompt == campaign_root / "page_prompt.txt"
    assert campaign_prompts.art_direction_template == art_dir / "brutalist.json"

    version_prompts = service.get_version_prompts("dreadmarsh", "dreadmarsh-crossing", "v002")
    assert version_prompts.page_prompt == episode_dir / "v002" / "prompts" / "page_prompt.txt"
    assert version_prompts.art_direction_template == episode_dir / "v002" / "art_direction_template.json"

    run_status = service.run_status("dreadmarsh", "dreadmarsh-crossing", "v002")
    assert run_status is not None
    assert run_status["status"] == "partial"

    # working/ is not a historical version, but is discoverable separately.
    assert service.has_working("dreadmarsh", "dreadmarsh-crossing") is False
    working = episode_dir / "working"
    working.mkdir()
    (working / "01_raw_text.json").write_text("{}", encoding="utf-8")
    assert service.has_working("dreadmarsh", "dreadmarsh-crossing") is True
    assert service.working_dir("dreadmarsh", "dreadmarsh-crossing") == working
    assert [v.version for v in service.list_versions("dreadmarsh", "dreadmarsh-crossing")] == [
        "v001",
        "v002",
    ]
    working_files = service.get_version_files("dreadmarsh", "dreadmarsh-crossing", "working")
    assert working_files.raw_text == working / "01_raw_text.json"


def test_list_episodes_uses_directory_name_when_meta_slugs_collide(tmp_path):
    campaigns_root = tmp_path / "campaigns"
    campaign_root = campaigns_root / "belowdown"
    live = campaign_root / "belowdown-ep-12"
    archive = campaign_root / "belowdown-ep-12_pre_entity_bible"
    for episode_dir, created_at in (
        (archive, "2026-06-13T16:24:46.013591+00:00"),
        (live, "2026-06-13T22:14:48.252712+00:00"),
    ):
        episode_dir.mkdir(parents=True)
        (episode_dir / "episode_meta.json").write_text(
            json.dumps(
                {
                    "url": "https://example.test/ep-12",
                    "slug": "belowdown-ep-12",
                    "title": "Belowdown Ep. 12",
                    "created_at": created_at,
                }
            ),
            encoding="utf-8",
        )
        _write_version(episode_dir / "v001")

    newest = campaign_root / "the-vault-of-the-once-great-thief-ep-3"
    newest.mkdir()
    (newest / "episode_meta.json").write_text(
        json.dumps(
            {
                "url": "https://example.test/ep-3",
                "slug": "the-vault-of-the-once-great-thief-ep-3",
                "title": "The Vault of the Once Great Thief Ep 3",
                "created_at": "2026-08-15T18:50:26.072604+00:00",
            }
        ),
        encoding="utf-8",
    )
    _write_version(newest / "v001")

    service = RepositoryService(campaigns_root)
    episodes = service.list_episodes("belowdown")

    assert [episode.slug for episode in episodes] == [
        "belowdown-ep-12_pre_entity_bible",
        "belowdown-ep-12",
        "the-vault-of-the-once-great-thief-ep-3",
    ]
    assert len({episode.slug for episode in episodes}) == 3
    assert service.list_versions("belowdown", "belowdown-ep-12_pre_entity_bible")


def test_list_versions_reads_starred_and_description_from_run_status(tmp_path):
    campaigns_root = tmp_path / "campaigns"
    episode_dir = campaigns_root / "dreadmarsh" / "dreadmarsh-crossing"
    _write_version(episode_dir / "v001", status="ok")
    _write_version(episode_dir / "v002", status="ok")
    status_path = episode_dir / "v002" / "run_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["starred"] = True
    status["description"] = "kept the swamp punchline"
    status_path.write_text(json.dumps(status), encoding="utf-8")

    service = RepositoryService(campaigns_root)
    versions = service.list_versions("dreadmarsh", "dreadmarsh-crossing")

    assert versions[0].starred is False
    assert versions[0].description == ""
    assert versions[1].starred is True
    assert versions[1].description == "kept the swamp punchline"
    assert versions[1].label == "★ v002"
    assert versions[0].label == "v001"


def test_update_version_meta_stars_and_describes_without_clobbering_status(tmp_path):
    campaigns_root = tmp_path / "campaigns"
    episode_dir = campaigns_root / "dreadmarsh" / "dreadmarsh-crossing"
    _write_version(episode_dir / "v001", status="partial")

    service = RepositoryService(campaigns_root)
    updated = service.update_version_meta(
        "dreadmarsh",
        "dreadmarsh-crossing",
        "v001",
        starred=True,
        description="  first readable bible  ",
    )

    assert updated.starred is True
    assert updated.description == "first readable bible"
    assert updated.status == "partial"

    on_disk = json.loads((episode_dir / "v001" / "run_status.json").read_text(encoding="utf-8"))
    assert on_disk["starred"] is True
    assert on_disk["description"] == "first readable bible"
    assert on_disk["status"] == "partial"
    assert on_disk["checkpoints"] == ["raw_text", "entities", "script"]

    unstarred = service.update_version_meta(
        "dreadmarsh",
        "dreadmarsh-crossing",
        "v001",
        starred=False,
    )
    assert unstarred.starred is False
    assert unstarred.description == "first readable bible"
    reread = service.list_versions("dreadmarsh", "dreadmarsh-crossing")[0]
    assert reread.starred is False
    assert reread.description == "first readable bible"


def test_update_version_meta_rejects_working_and_missing_status(tmp_path):
    campaigns_root = tmp_path / "campaigns"
    episode_dir = campaigns_root / "dreadmarsh" / "dreadmarsh-crossing"
    _write_version(episode_dir / "v001")
    working = episode_dir / "working"
    working.mkdir()
    (working / "run_status.json").write_text(
        json.dumps({"status": "ok", "checkpoints": [], "failed": [], "errors": []}),
        encoding="utf-8",
    )

    service = RepositoryService(campaigns_root)

    with pytest.raises(ValueError, match="historical"):
        service.update_version_meta(
            "dreadmarsh", "dreadmarsh-crossing", "working", starred=True
        )
    with pytest.raises(ValueError, match="historical"):
        service.update_version_meta(
            "dreadmarsh", "dreadmarsh-crossing", "scratch", description="nope"
        )

    missing = episode_dir / "v002"
    missing.mkdir()
    with pytest.raises(FileNotFoundError, match="run_status"):
        service.update_version_meta(
            "dreadmarsh", "dreadmarsh-crossing", "v002", starred=True
        )

    # working/run_status.json must stay unstarred even if a caller tries.
    working_status = json.loads((working / "run_status.json").read_text(encoding="utf-8"))
    assert "starred" not in working_status
