"""Filesystem-backed discovery helpers for campaigns, episodes, versions, and prompts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from art_styles import (
    ART_DIRECTION_TEMPLATE_FILENAME,
    DEFAULT_ART_STYLE_STEM,
    campaign_art_direction_dir,
)
from prompt_templates import (
    STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
    STORY_ARCHITECT_USER_PROMPT_FILENAME,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)
EPISODE_META_FILENAME = "episode_meta.json"
RUN_STATUS_FILENAME = "run_status.json"
WORKING_DIR_NAME = "working"
IMAGES_DIR_NAME = "images"
IMAGE_FILE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VERSION_PATTERN = re.compile(r"v\d{3}")
PREFERRED_VERSION_FILES = (
    "creative_direction.txt",
    "01_raw_text.json",
    "02_entities.json",
    "02_5_story_bible.txt",
    "03_script.json",
    "03_5_styled_script.json",
    "04_page_1_prompt.txt",
    "run_status.json",
    "art_direction_template.json",
)


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
    starred: bool = False
    description: str = ""

    @property
    def label(self) -> str:
        return f"★ {self.version}" if self.starred else self.version


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
class VersionFileEntry:
    key: str
    kind: str
    exists: bool


@dataclass(frozen=True)
class CampaignPrompts:
    art_direction_template: Path
    story_architect_system: Path
    story_architect_user: Path
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
                    slug=episode_dir.name,
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

    def working_dir(self, campaign: str, episode_slug: str) -> Path | None:
        """Return the episode working directory if it exists."""
        path = self.campaigns_root / campaign / episode_slug / WORKING_DIR_NAME
        return path if path.is_dir() else None

    def has_working(self, campaign: str, episode_slug: str) -> bool:
        return self.working_dir(campaign, episode_slug) is not None

    def list_versions(self, campaign: str, episode_slug: str) -> list[VersionInfo]:
        """List immutable historical versions (vNNN only; excludes working/)."""
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
            versions.append(self._version_info(version_dir, status_data))
        return versions

    def update_version_meta(
        self,
        campaign: str,
        episode_slug: str,
        version: str,
        *,
        starred: bool | None = None,
        description: str | None = None,
    ) -> VersionInfo:
        """Update favorite/note fields on a historical version's run_status.json."""
        if not VERSION_PATTERN.fullmatch(version):
            raise ValueError(
                f"can only update metadata for historical versions, not {version!r}"
            )
        status_path = (
            self.campaigns_root / campaign / episode_slug / version / RUN_STATUS_FILENAME
        )
        if not status_path.exists():
            raise FileNotFoundError(f"run_status.json not found for {version}")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if starred is not None:
            status["starred"] = bool(starred)
        if description is not None:
            status["description"] = description.strip()
        status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        return self._version_info(status_path.parent, status)

    def list_version_files(
        self, campaign: str, episode_slug: str, version: str
    ) -> list[VersionFileEntry]:
        """Return version files as relative keys, matching Output-tab order."""
        version_dir = self._version_dir(campaign, episode_slug, version)
        entries: list[VersionFileEntry] = []
        seen: set[str] = set()

        for name in PREFERRED_VERSION_FILES:
            path = version_dir / name
            exists = path.is_file()
            if exists or (
                name == "creative_direction.txt" and version == WORKING_DIR_NAME
            ):
                entries.append(_file_entry(name, exists=exists))
                seen.add(name)

        if version_dir.is_dir():
            extras = sorted(
                path
                for path in version_dir.iterdir()
                if path.is_file() and path.name not in seen
            )
            for path in extras:
                entries.append(_file_entry(path.name, exists=True))
                seen.add(path.name)

            images_root = version_dir / IMAGES_DIR_NAME
            if images_root.is_dir():
                for folder in sorted(p for p in images_root.iterdir() if p.is_dir()):
                    for path in sorted(p for p in folder.iterdir() if p.is_file()):
                        key = path.relative_to(version_dir).as_posix()
                        entries.append(_file_entry(key, exists=True))

        return entries

    def resolve_version_file(
        self, campaign: str, episode_slug: str, version: str, key: str
    ) -> Path:
        """Resolve a relative file key under a version dir. Path may not exist."""
        version_dir = self._version_dir(campaign, episode_slug, version)
        relative = safe_relative_key(key)
        path = (version_dir / relative).resolve()
        try:
            path.relative_to(version_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"invalid file key {key!r}") from exc
        return path

    def get_version_files(self, campaign: str, episode_slug: str, version: str) -> VersionFiles:
        # version may be a historical vNNN or the special WORKING_DIR_NAME label.
        version_dir = self.campaigns_root / campaign / episode_slug / version
        prompts_dir = version_dir / "prompts"
        return VersionFiles(
            version_dir=version_dir,
            raw_text=self._path_if_exists(version_dir / "01_raw_text.json"),
            entities=self._path_if_exists(version_dir / "02_entities.json"),
            story_bible=self._path_if_exists(version_dir / "02_5_story_bible.txt"),
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
        art_dir = campaign_art_direction_dir(self.campaigns_root, campaign)
        campaign_styles = (
            sorted(
                p
                for p in art_dir.glob("*.json")
                if p.is_file() and not p.stem.startswith("_")
            )
            if art_dir.exists()
            else []
        )
        # Prefer an existing campaign style for editing; otherwise a campaign save target.
        art_path = (
            campaign_styles[0]
            if campaign_styles
            else art_dir / f"{DEFAULT_ART_STYLE_STEM}.json"
        )
        return CampaignPrompts(
            art_direction_template=art_path,
            story_architect_system=campaign_root / STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
            story_architect_user=campaign_root / STORY_ARCHITECT_USER_PROMPT_FILENAME,
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
            story_architect_system=prompts_dir / STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
            story_architect_user=prompts_dir / STORY_ARCHITECT_USER_PROMPT_FILENAME,
            scriptwriter_system=prompts_dir / SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
            scriptwriter_user=prompts_dir / SCRIPTWRITER_USER_PROMPT_FILENAME,
            style_integrator_system=prompts_dir / STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
            style_integrator_user=prompts_dir / STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
            page_prompt=prompts_dir / PAGE_PROMPT_TEMPLATE_FILENAME,
        )

    def version_has_images(self, campaign: str, episode_slug: str, version: str) -> bool:
        images_root = self.campaigns_root / campaign / episode_slug / version / IMAGES_DIR_NAME
        return _dir_has_image_files(images_root)

    def episode_has_images(self, campaign: str, episode_slug: str) -> bool:
        episode_dir = self.campaigns_root / campaign / episode_slug
        if not episode_dir.is_dir():
            return False
        return any(
            child.is_dir() and self.version_has_images(campaign, episode_slug, child.name)
            for child in episode_dir.iterdir()
        )

    def run_status(self, campaign: str, episode_slug: str, version: str) -> dict[str, Any] | None:
        status_path = self.campaigns_root / campaign / episode_slug / version / RUN_STATUS_FILENAME
        if not status_path.exists():
            return None
        return json.loads(status_path.read_text(encoding="utf-8"))

    @staticmethod
    def _version_info(version_dir: Path, status_data: dict[str, Any]) -> VersionInfo:
        return VersionInfo(
            version=version_dir.name,
            version_dir=version_dir,
            status=status_data.get("status"),
            created_at=status_data.get("created_at"),
            checkpoints=list(status_data.get("checkpoints", [])),
            failed=list(status_data.get("failed", [])),
            errors=list(status_data.get("errors", [])),
            starred=bool(status_data.get("starred", False)),
            description=str(status_data.get("description") or ""),
        )

    def _version_dir(self, campaign: str, episode_slug: str, version: str) -> Path:
        _require_segment(campaign, "campaign")
        _require_segment(episode_slug, "episode")
        if version != WORKING_DIR_NAME and not VERSION_PATTERN.fullmatch(version):
            raise ValueError(f"invalid version {version!r}")
        return self.campaigns_root / campaign / episode_slug / version

    @staticmethod
    def _path_if_exists(path: Path) -> Path | None:
        return path if path.exists() else None


def _require_segment(value: str, label: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid {label} {value!r}")


def safe_relative_key(key: str) -> Path:
    if not key or key.startswith(("/", "\\")) or "\\" in key:
        raise ValueError(f"invalid file key {key!r}")
    relative = Path(key)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[0] == "..":
        raise ValueError(f"invalid file key {key!r}")
    return relative


def _file_kind(key: str) -> str:
    suffix = Path(key).suffix.lower()
    return "image" if suffix in IMAGE_FILE_SUFFIXES else "text"


def _file_entry(key: str, *, exists: bool) -> VersionFileEntry:
    return VersionFileEntry(key=key, kind=_file_kind(key), exists=exists)


def _dir_has_image_files(images_root: Path) -> bool:
    if not images_root.is_dir():
        return False
    return any(
        path.is_file() and path.suffix.lower() in IMAGE_FILE_SUFFIXES
        for path in images_root.rglob("*")
    )