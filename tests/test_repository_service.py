from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from repository_service import RepositoryService


def _write_version(version_dir: Path, status: str = "ok") -> None:
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "01_raw_text.json").write_text("{}", encoding="utf-8")
    (version_dir / "02_entities.json").write_text("{}", encoding="utf-8")
    (version_dir / "02_5_story_bible.json").write_text("{}", encoding="utf-8")
    (version_dir / "03_script.json").write_text("{}", encoding="utf-8")
    (version_dir / "03_5_styled_script.json").write_text("{}", encoding="utf-8")
    (version_dir / "04_page_prompt.txt").write_text("prompt", encoding="utf-8")
    (version_dir / "art_direction_template.json").write_text("{}", encoding="utf-8")
    prompts_dir = version_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    for filename in (
        "master_beater_system.txt",
        "master_beater_user.txt",
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
    (campaign_root / "art_direction_template.json").write_text("{}", encoding="utf-8")
    for filename in (
        "master_beater_system.txt",
        "master_beater_user.txt",
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

    files = service.get_version_files("dreadmarsh", "dreadmarsh-crossing", "v002")
    assert files.raw_text == episode_dir / "v002" / "01_raw_text.json"
    assert files.page_prompt == episode_dir / "v002" / "04_page_prompt.txt"
    assert files.prompts_dir == episode_dir / "v002" / "prompts"

    campaign_prompts = service.get_campaign_prompts("dreadmarsh")
    assert campaign_prompts.page_prompt == campaign_root / "page_prompt.txt"
    assert campaign_prompts.art_direction_template == campaign_root / "art_direction_template.json"

    version_prompts = service.get_version_prompts("dreadmarsh", "dreadmarsh-crossing", "v002")
    assert version_prompts.page_prompt == episode_dir / "v002" / "prompts" / "page_prompt.txt"
    assert version_prompts.art_direction_template == episode_dir / "v002" / "art_direction_template.json"

    run_status = service.run_status("dreadmarsh", "dreadmarsh-crossing", "v002")
    assert run_status is not None
    assert run_status["status"] == "partial"
