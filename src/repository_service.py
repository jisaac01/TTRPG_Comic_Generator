"""Filesystem-backed discovery helpers for campaigns, episodes, versions, and prompts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_templates import (
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)

ART_DIRECTION_TEMPLATE_FILENAME = "art_direction_template.json"
EPISODE_META_FILENAME = "episode_meta.json"
RUN_STATUS_FILENAME = "run_status.json"
VERSION_PATTERN = re.compile(r"v\d{3}")


@dataclass(frozen=True)
class Episode:
    campaign: str
    slug: str
    url: str | None
    title: str | None
    created_at: str | None
    path: Path


@dataclass(frozen=True)
class VersionInfo:
    version: str
    version_dir: Path
    status: str | None
    created_at: str | None
    checkpoints: list[str]
    failed: list[str]
    errors: list[str]


@dataclass(frozen=True)
class VersionFiles:
    version_dir: Path
    raw_text: Path | None
    entities: Path | None
    story_bible: Path | None
    script: Path | None
    styled_script: Path | None
    page_prompt: Path | None
    art_direction_template: Path | None
    prompts_dir: Path | None


@dataclass(frozen=True)
class CampaignPrompts:
    art_direction_template: Path
    master_beater_system: Path
    master_beater_user: Path
    scriptwriter_system: Path
    scriptwriter_user: Path
    style_integrator_system: Path
    style_integrator_user: Path
    page_prompt: Path


class RepositoryService:
    def __init__(self, campaigns_root: Path) -> None:
        self.campaigns_root = campaigns_root

    def create_campaign(self, campaign: str) -> Path:
        name = campaign.strip()
        if not name:
            raise ValueError("campaign name cannot be empty")
        if any(sep in name for sep in ("/", "\\")):
            raise ValueError("campaign name cannot contain path separators")

        path = self.campaigns_root / name
        path.mkdir(parents=True, exist_ok=False)
        return path

    def list_campaigns(self) -> list[str]:
        if not self.campaigns_root.exists():
            return []
        return sorted(
            entry.name for entry in self.campaigns_root.iterdir() if entry.is_dir()
        )

    def list_episodes(self, campaign: str) -> list[Episode]:
        campaign_root = self.campaigns_root / campaign
        if not campaign_root.exists():
            return []

        episodes: list[Episode] = []
        for episode_dir in sorted(entry for entry in campaign_root.iterdir() if entry.is_dir()):
            meta_path = episode_dir / EPISODE_META_FILENAME
            if not meta_path.exists():
                continue

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            episodes.append(
                Episode(
                    campaign=campaign,
                    slug=meta.get("slug", episode_dir.name),
                    url=meta.get("url"),
                    title=meta.get("title"),
                    created_at=meta.get("created_at"),
                    path=episode_dir,
                )
            )

        return sorted(episodes, key=lambda episode: (episode.created_at or "", episode.slug))

    def latest_version(self, campaign: str, episode_slug: str) -> str | None:
        versions = self.list_versions(campaign, episode_slug)
        return versions[-1].version if versions else None

    def list_versions(self, campaign: str, episode_slug: str) -> list[VersionInfo]:
        episode_dir = self.campaigns_root / campaign / episode_slug
        if not episode_dir.exists():
            return []

        version_dirs = sorted(
            (
                entry
                for entry in episode_dir.iterdir()
                if entry.is_dir() and VERSION_PATTERN.fullmatch(entry.name)
            ),
            key=lambda path: int(path.name[1:]),
        )
        versions: list[VersionInfo] = []
        for version_dir in version_dirs:
            status_data = self.run_status(campaign, episode_slug, version_dir.name) or {}
            versions.append(
                VersionInfo(
                    version=version_dir.name,
                    version_dir=version_dir,
                    status=status_data.get("status"),
                    created_at=status_data.get("created_at"),
                    checkpoints=list(status_data.get("checkpoints", [])),
                    failed=list(status_data.get("failed", [])),
                    errors=list(status_data.get("errors", [])),
                )
            )
        return versions

    def get_version_files(self, campaign: str, episode_slug: str, version: str) -> VersionFiles:
        version_dir = self.campaigns_root / campaign / episode_slug / version
        prompts_dir = version_dir / "prompts"
        return VersionFiles(
            version_dir=version_dir,
            raw_text=self._path_if_exists(version_dir / "01_raw_text.json"),
            entities=self._path_if_exists(version_dir / "02_entities.json"),
            story_bible=self._path_if_exists(version_dir / "02_5_story_bible.json"),
            script=self._path_if_exists(version_dir / "03_script.json"),
            styled_script=self._path_if_exists(version_dir / "03_5_styled_script.json"),
            page_prompt=self._path_if_exists(version_dir / "04_page_1_prompt.txt"),
            art_direction_template=self._path_if_exists(
                version_dir / ART_DIRECTION_TEMPLATE_FILENAME
            ),
            prompts_dir=prompts_dir if prompts_dir.exists() else None,
        )

    def get_campaign_prompts(self, campaign: str) -> CampaignPrompts:
        campaign_root = self.campaigns_root / campaign
        return CampaignPrompts(
            art_direction_template=campaign_root / ART_DIRECTION_TEMPLATE_FILENAME,
            master_beater_system=campaign_root / MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
            master_beater_user=campaign_root / MASTER_BEATER_USER_PROMPT_FILENAME,
            scriptwriter_system=campaign_root / SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
            scriptwriter_user=campaign_root / SCRIPTWRITER_USER_PROMPT_FILENAME,
            style_integrator_system=campaign_root / STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
            style_integrator_user=campaign_root / STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
            page_prompt=campaign_root / PAGE_PROMPT_TEMPLATE_FILENAME,
        )

    def get_version_prompts(self, campaign: str, episode_slug: str, version: str) -> CampaignPrompts:
        version_dir = self.campaigns_root / campaign / episode_slug / version
        prompts_dir = version_dir / "prompts"
        return CampaignPrompts(
            art_direction_template=version_dir / ART_DIRECTION_TEMPLATE_FILENAME,
            master_beater_system=prompts_dir / MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
            master_beater_user=prompts_dir / MASTER_BEATER_USER_PROMPT_FILENAME,
            scriptwriter_system=prompts_dir / SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
            scriptwriter_user=prompts_dir / SCRIPTWRITER_USER_PROMPT_FILENAME,
            style_integrator_system=prompts_dir / STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
            style_integrator_user=prompts_dir / STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
            page_prompt=prompts_dir / PAGE_PROMPT_TEMPLATE_FILENAME,
        )

    def run_status(self, campaign: str, episode_slug: str, version: str) -> dict[str, Any] | None:
        status_path = self.campaigns_root / campaign / episode_slug / version / RUN_STATUS_FILENAME
        if not status_path.exists():
            return None
        return json.loads(status_path.read_text(encoding="utf-8"))

    @staticmethod
    def _path_if_exists(path: Path) -> Path | None:
        return path if path.exists() else None