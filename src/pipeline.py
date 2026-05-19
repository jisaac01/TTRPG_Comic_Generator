from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, cast

from entities import (
    WorldStateCheckpoint,
    build_entities_from_raw,
)
from model_defaults import DEFAULT_MODEL
from pipeline_config import RunConfig, RerunFrom, CAMPAIGNS_ROOT
from pipeline_events import (
    PipelineEvent,
    PipelineEventUnion,
    PhaseStarted,
    PhaseSkipped,
    PhaseCompleted,
    PhaseWarning,
    PhaseError,
    PhasePartialFailure,
    VersionCreated,
    RunCompleted,
)
from prompter import (
    ART_DIRECTION_TEMPLATE_FILENAME,
    DEFAULT_ART_DIRECTION_TEMPLATE_PATH,
    _load_art_template,
)
from prompt_saver import (
    prepare_beater_prompts,
    prepare_page_prompt_template,
    prepare_scriptwriter_prompts,
    prepare_style_integrator_prompts,
)
from prompt_templates import (
    DEFAULT_PROMPTS_DIR,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    PROMPT_TEMPLATE_FILENAMES,
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)
from scriptwriter import WorldStateInput
from style_integrator import StyleIntegrationPartialFailure, integrate_style
from scraper import RawTextCheckpoint, normalize_recap_version, scrape_scrybequill
from scriptwriter import (
    ScriptCheckpoint,
    apply_cross_page_continuity_errors,
    renumber_script_page_checkpoints,
    write_script,
    write_story_bible_pages,
)
from master_beater import StoryBibleCheckpoint, create_story_bible

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_FILENAME = "index.json"
EPISODE_META_FILENAME = "episode_meta.json"
PROMPTS_SUBDIR_NAME = "prompts"
STORY_BIBLE_PAGE_GLOB = "02_6_story_bible_page_*.json"
SCRIPT_PAGE_GLOB = "03_script_page_*.json"
STYLED_SCRIPT_PAGE_GLOB = "03_5_styled_script_page_*.json"

# RerunFrom is imported from pipeline_config above


def _story_bible_page_path(version_dir: Path, page_number: int) -> Path:
    return version_dir / f"02_6_story_bible_page_{page_number:03d}.json"


def _script_page_path(version_dir: Path, page_number: int) -> Path:
    return version_dir / f"03_script_page_{page_number:03d}.json"


def _styled_script_page_path(version_dir: Path, page_number: int) -> Path:
    return version_dir / f"03_5_styled_script_page_{page_number:03d}.json"


def _copy_checkpoint_patterns(prev_dir: Path, version_dir: Path, patterns: list[str]) -> None:
    for pattern in patterns:
        if "*" in pattern:
            for src in prev_dir.glob(pattern):
                shutil.copy2(src, version_dir / src.name)
            continue

        src = prev_dir / pattern
        if src.exists():
            shutil.copy2(src, version_dir / src.name)


def _load_script_pages(paths: list[Path]) -> list[ScriptCheckpoint]:
    return [
        ScriptCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
        for path in paths
    ]


def _write_script_pages(paths: list[Path], checkpoints: list[ScriptCheckpoint]) -> None:
    if len(paths) != len(checkpoints):
        raise ValueError(f"Expected {len(paths)} checkpoints, received {len(checkpoints)}.")
    for path, checkpoint in zip(paths, checkpoints):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")


def _delete_matching(version_dir: Path, pattern: str) -> None:
    for path in version_dir.glob(pattern):
        path.unlink(missing_ok=True)


def _format_exception_detail(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert a story title to a safe folder name slug."""
    text = text.lower().strip()
    # Replace non-word, non-whitespace chars (e.g. em-dash) with spaces first.
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = text.strip("-")
    return text or "episode"


# ---------------------------------------------------------------------------
# Index helpers (campaigns/index.json)
# ---------------------------------------------------------------------------


def _read_index(campaigns_root: Path) -> dict:
    path = campaigns_root / INDEX_FILENAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write_index_atomic(campaigns_root: Path, index: dict) -> None:
    """Write index.json atomically using a temp file + rename."""
    campaigns_root.mkdir(parents=True, exist_ok=True)
    target = campaigns_root / INDEX_FILENAME
    fd, tmp_name = tempfile.mkstemp(dir=campaigns_root, prefix=".index_", suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=2, ensure_ascii=False)
        Path(tmp_name).replace(target)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _index_key(campaign: str, url: str) -> str:
    return f"{campaign}::{url}"


def _lookup_episode(campaigns_root: Path, campaign: str, url: str) -> str | None:
    """Return the episode folder name for (campaign, url) or None if not found."""
    index = _read_index(campaigns_root)
    return index.get(_index_key(campaign, url))


def _register_episode(
    campaigns_root: Path, campaign: str, url: str, episode_folder: str
) -> None:
    index = _read_index(campaigns_root)
    index[_index_key(campaign, url)] = episode_folder
    _write_index_atomic(campaigns_root, index)


# ---------------------------------------------------------------------------
# Episode + version path resolution
# ---------------------------------------------------------------------------


def _episode_dir(campaigns_root: Path, campaign: str, episode_folder: str) -> Path:
    return campaigns_root / campaign / episode_folder


def _resolve_episode_dir(
    campaigns_root: Path, campaign: str, url: str, title: str | None
) -> Path:
    """Find or create the episode directory, keyed canonically by URL."""
    existing = _lookup_episode(campaigns_root, campaign, url)
    if existing:
        return _episode_dir(campaigns_root, campaign, existing)

    # First run for this campaign + URL: create episode folder from title slug.
    slug = _slugify(title) if title else "episode"
    campaign_root = campaigns_root / campaign
    campaign_root.mkdir(parents=True, exist_ok=True)

    # Avoid collisions with existing folders if another episode has the same slug.
    candidate = slug
    counter = 2
    while (campaign_root / candidate).exists():
        candidate = f"{slug}-{counter}"
        counter += 1

    episode_path = campaign_root / candidate
    episode_path.mkdir(parents=True, exist_ok=True)

    # Write episode metadata.
    meta = {
        "url": url,
        "slug": candidate,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (episode_path / EPISODE_META_FILENAME).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    _register_episode(campaigns_root, campaign, url, candidate)
    return episode_path


def _next_version_name(episode_dir: Path) -> str:
    """Return the next auto-incremented version label (v001, v002, ...)."""
    existing = sorted(
        p.name for p in episode_dir.iterdir() if p.is_dir() and re.fullmatch(r"v\d{3}", p.name)
    )
    if not existing:
        return "v001"
    last_num = int(existing[-1][1:])
    return f"v{last_num + 1:03d}"


def _create_version_dir(
    episode_dir: Path, rerun_from: RerunFrom | None
) -> tuple[Path, str]:
    """
    Create the next version directory.

    If a previous version exists, copy only the specific checkpoint files
    that should be preserved (those prior to *rerun_from*). Prompt template files
    and art direction templates are always regenerated during the run and are not copied.
    
    If rerun_from is None, all checkpoint files are preserved from the previous version.

    Returns (version_dir, version_name).
    """
    version_name = _next_version_name(episode_dir)
    version_dir = episode_dir / version_name
    version_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(
        p for p in episode_dir.iterdir() if p.is_dir() and re.fullmatch(r"v\d{3}", p.name)
        and p.name != version_name
    )
    if existing:
        prev_dir = existing[-1]
        
        # Map each rerun point to the checkpoint files that should be preserved.
        # Prompt template files and art_direction_template are resolved fresh during run()
        # via _capture_prompt_templates_for_version and _capture_art_template_for_version,
        # so they are never copied from the previous version.
        _CHECKPOINTS_TO_COPY: dict[RerunFrom | None, list[str]] = {
            None: [
                "01_raw_text.json",
                "02_entities.json",
                "02_5_story_bible.json",
                STORY_BIBLE_PAGE_GLOB,
                SCRIPT_PAGE_GLOB,
                STYLED_SCRIPT_PAGE_GLOB,
            ],
            "scrape": [],
            "entities": ["01_raw_text.json"],
            "beater": ["01_raw_text.json", "02_entities.json"],
            "script": ["01_raw_text.json", "02_entities.json", "02_5_story_bible.json"],
            "style": [
                "01_raw_text.json",
                "02_entities.json",
                "02_5_story_bible.json",
                STORY_BIBLE_PAGE_GLOB,
                SCRIPT_PAGE_GLOB,
            ],
            "prompt": [
                "01_raw_text.json",
                "02_entities.json",
                "02_5_story_bible.json",
                STORY_BIBLE_PAGE_GLOB,
                SCRIPT_PAGE_GLOB,
                STYLED_SCRIPT_PAGE_GLOB,
            ],
        }
        files_to_copy = _CHECKPOINTS_TO_COPY.get(rerun_from, [])

        _copy_checkpoint_patterns(prev_dir, version_dir, files_to_copy)

        # Prompt reruns must regenerate 04_page_*.txt from the current script state.
        if rerun_from is None:
            for prev_prompt_file in prev_dir.glob("04_page_*.txt"):
                shutil.copy2(prev_prompt_file, version_dir / prev_prompt_file.name)

            prev_prompts_dir = prev_dir / PROMPTS_SUBDIR_NAME
            if prev_prompts_dir.exists():
                shutil.copytree(prev_prompts_dir, version_dir / PROMPTS_SUBDIR_NAME)

    return version_dir, version_name


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class ComicPipeline:
    def __init__(
        self,
        url: str,
        campaign: str,
        campaigns_root: Path = CAMPAIGNS_ROOT,
        beater_model: str = DEFAULT_MODEL,
        script_model: str = DEFAULT_MODEL,
        style_model: str = DEFAULT_MODEL,
        panel_count: int = 6,
        total_pages: int = 1,
        art_style_template: Path | None = None,
        master_beater_system_prompt: Path | None = None,
        master_beater_user_prompt: Path | None = None,
        scriptwriter_system_prompt: Path | None = None,
        scriptwriter_user_prompt: Path | None = None,
        style_integrator_system_prompt: Path | None = None,
        style_integrator_user_prompt: Path | None = None,
        page_prompt_template: Path | None = None,
        rerun_from: RerunFrom | None = None,
        recap_version: str = "standard",
        skip_style: bool = False,
        event_callback: Callable[[PipelineEventUnion], None] | None = None,
    ):
        self.url = url
        self.campaign = campaign
        self.campaigns_root = campaigns_root
        self.beater_model = beater_model
        self.script_model = script_model
        self.style_model = style_model
        self.panel_count = panel_count
        self.total_pages = total_pages
        self.art_style_template = art_style_template
        self.master_beater_system_prompt = master_beater_system_prompt
        self.master_beater_user_prompt = master_beater_user_prompt
        self.scriptwriter_system_prompt = scriptwriter_system_prompt
        self.scriptwriter_user_prompt = scriptwriter_user_prompt
        self.style_integrator_system_prompt = style_integrator_system_prompt
        self.style_integrator_user_prompt = style_integrator_user_prompt
        self.page_prompt_template = page_prompt_template
        self.rerun_from: RerunFrom | None = rerun_from
        self.recap_version = normalize_recap_version(recap_version)
        self.skip_style = skip_style
        self.event_callback = event_callback or (lambda _: None)
        self._version_dir: Path | None = None

    def _emit(self, event: PipelineEventUnion) -> None:
        """Emit an event via the callback."""
        self.event_callback(event)

    def _apply_recap_selection(self, raw: RawTextCheckpoint) -> tuple[RawTextCheckpoint, bool, bool]:
        """Select content from recap variants and report selection/content changes."""
        selected = self.recap_version
        variants = raw.recap_variants or {}
        chosen = variants.get(selected)
        if not chosen:
            return raw, False, False

        content_changed = raw.content != chosen
        selection_changed = raw.selected_recap != selected
        if not content_changed and not selection_changed:
            return raw, False, False

        updated = raw.model_copy(
            update={
                "content": chosen,
                "selected_recap": selected,
            }
        )
        return updated, content_changed, True

    def _resolve_art_template(self, version_dir: Path, episode_dir: Path) -> Path:
        """Resolve art style template: explicit > campaign-level > inline default."""
        if self.art_style_template is not None:
            return self.art_style_template
        campaign_template = (
            self.campaigns_root / self.campaign / ART_DIRECTION_TEMPLATE_FILENAME
        )
        if campaign_template.exists():
            return campaign_template
        # Fall back to a template in the version dir if one was cloned from a prior version.
        version_template = version_dir / ART_DIRECTION_TEMPLATE_FILENAME
        if version_template.exists():
            return version_template
        # Last resort: same directory as this script (legacy support during migration).
        return Path(ART_DIRECTION_TEMPLATE_FILENAME)

    def _campaign_prompt_path(self, filename: str) -> Path:
        return self.campaigns_root / self.campaign / filename

    def _ensure_campaign_prompt_templates(self) -> None:
        """Create campaign prompt template copies when they do not already exist."""
        for filename in PROMPT_TEMPLATE_FILENAMES:
            campaign_prompt = self._campaign_prompt_path(filename)
            if campaign_prompt.exists():
                continue
            campaign_prompt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DEFAULT_PROMPTS_DIR / filename, campaign_prompt)

    def _resolve_prompt_templates(self) -> dict[str, Path]:
        return {
            MASTER_BEATER_SYSTEM_PROMPT_FILENAME: self.master_beater_system_prompt
            or self._campaign_prompt_path(MASTER_BEATER_SYSTEM_PROMPT_FILENAME),
            MASTER_BEATER_USER_PROMPT_FILENAME: self.master_beater_user_prompt
            or self._campaign_prompt_path(MASTER_BEATER_USER_PROMPT_FILENAME),
            SCRIPTWRITER_SYSTEM_PROMPT_FILENAME: self.scriptwriter_system_prompt
            or self._campaign_prompt_path(SCRIPTWRITER_SYSTEM_PROMPT_FILENAME),
            SCRIPTWRITER_USER_PROMPT_FILENAME: self.scriptwriter_user_prompt
            or self._campaign_prompt_path(SCRIPTWRITER_USER_PROMPT_FILENAME),
            STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME: self.style_integrator_system_prompt
            or self._campaign_prompt_path(STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME),
            STYLE_INTEGRATOR_USER_PROMPT_FILENAME: self.style_integrator_user_prompt
            or self._campaign_prompt_path(STYLE_INTEGRATOR_USER_PROMPT_FILENAME),
            PAGE_PROMPT_TEMPLATE_FILENAME: self.page_prompt_template
            or self._campaign_prompt_path(PAGE_PROMPT_TEMPLATE_FILENAME),
        }

    def _capture_prompt_templates_for_version(
        self,
        prompt_paths: dict[str, Path],
        version_dir: Path,
    ) -> dict[str, Path]:
        """Copy prompt templates into the version directory and return their version-local paths."""
        prompts_dir = version_dir / PROMPTS_SUBDIR_NAME
        prompts_dir.mkdir(parents=True, exist_ok=True)
        captured_paths: dict[str, Path] = {}
        for filename, source_path in prompt_paths.items():
            if not source_path.exists():
                raise FileNotFoundError(
                    f"Prompt template file not found at {source_path}."
                )

            version_prompt_path = prompts_dir / filename
            if source_path != version_prompt_path:
                shutil.copy2(source_path, version_prompt_path)
            captured_paths[filename] = version_prompt_path
        return captured_paths

    def _capture_art_template_for_version(self, template_path: Path, version_dir: Path) -> Path:
        """Copy the resolved template into the version directory and return that path."""
        version_template_path = version_dir / ART_DIRECTION_TEMPLATE_FILENAME

        if not template_path.exists():
            return template_path

        if template_path == version_template_path:
            return version_template_path

        shutil.copy2(template_path, version_template_path)
        return version_template_path

    def _ensure_campaign_art_template(self) -> None:
        """Create a default campaign art template if one does not already exist."""
        if self.art_style_template is not None:
            return

        campaign_template = (
            self.campaigns_root / self.campaign / ART_DIRECTION_TEMPLATE_FILENAME
        )
        if campaign_template.exists():
            return

        campaign_template.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEFAULT_ART_DIRECTION_TEMPLATE_PATH, campaign_template)

    async def run(self) -> dict[str, object]:
        self._version_dir: Path | None = None
        # Phase 1: scrape first so we have the title for episode resolution.
        # We need a temporary path to store the raw checkpoint before the episode
        # directory is resolved (title comes from the scrape).
        #
        # Strategy: scrape into a temp directory first if no episode exists yet,
        # then resolve the episode dir, then move the file into the version dir.

        existing_episode = _lookup_episode(self.campaigns_root, self.campaign, self.url)

        if existing_episode is None:
            # First run: scrape to get the title so we can slug the episode folder.
            self._emit(PhaseStarted(phase="scrape", message="Scraping..."))
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_raw_path = Path(tmpdir) / "01_raw_text.json"
                raw = await scrape_scrybequill(
                    url=self.url,
                    checkpoint_path=tmp_raw_path,
                    recap_version=self.recap_version,
                )
            self._emit(
                PhaseCompleted(
                    phase="scrape",
                    message="...done",
                    details={"title": raw.title, "recap": self.recap_version},
                )
            )

            episode_dir = _resolve_episode_dir(
                self.campaigns_root, self.campaign, self.url, raw.title
            )
            version_dir, version_name = _create_version_dir(episode_dir, self.rerun_from)
            self._version_dir = version_dir
            self._emit(
                VersionCreated(
                    version=version_name,
                    version_dir=str(version_dir),
                    episode_slug=episode_dir.name,
                )
            )

            raw_path = version_dir / "01_raw_text.json"
            raw_path.write_text(
                json.dumps(raw.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
            )

        else:
            episode_dir = _episode_dir(self.campaigns_root, self.campaign, existing_episode)
            version_dir, version_name = _create_version_dir(episode_dir, self.rerun_from)
            self._version_dir = version_dir
            self._emit(
                VersionCreated(
                    version=version_name,
                    version_dir=str(version_dir),
                    episode_slug=episode_dir.name,
                )
            )
            raw_path = version_dir / "01_raw_text.json"

            if raw_path.exists():
                raw = RawTextCheckpoint.model_validate_json(raw_path.read_text(encoding="utf-8"))
                selected_raw, content_changed, selection_updated = self._apply_recap_selection(raw)
                if selection_updated:
                    raw = selected_raw
                    raw_path.write_text(
                        json.dumps(raw.model_dump(), indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                if content_changed:
                    self._emit(
                        PhaseWarning(
                            phase="scrape",
                            message="Recap variant changed - invalidating downstream checkpoints",
                            warning=f"Recap variant changed to {self.recap_version!r}",
                        )
                    )
                    entities_path = version_dir / "02_entities.json"
                    entities_path.unlink(missing_ok=True)
                    _delete_matching(version_dir, STORY_BIBLE_PAGE_GLOB)
                    _delete_matching(version_dir, SCRIPT_PAGE_GLOB)
                    _delete_matching(version_dir, STYLED_SCRIPT_PAGE_GLOB)
                    for prompt_file in version_dir.glob("04_page_*.txt"):
                        prompt_file.unlink(missing_ok=True)
                    shutil.rmtree(version_dir / PROMPTS_SUBDIR_NAME, ignore_errors=True)
                else:
                    self._emit(
                        PhaseSkipped(
                            phase="scrape",
                            message="Skipped",
                            reason="checkpoint exists",
                        )
                    )
            else:
                self._emit(PhaseStarted(phase="scrape", message="Scraping..."))
                raw = await scrape_scrybequill(
                    url=self.url,
                    checkpoint_path=raw_path,
                    recap_version=self.recap_version,
                )
                self._emit(
                    PhaseCompleted(
                        phase="scrape",
                        message="...done",
                        details={"title": raw.title, "recap": self.recap_version},
                    )
                )

        self._ensure_campaign_art_template()
        self._ensure_campaign_prompt_templates()
        entities_path = version_dir / "02_entities.json"
        story_bible_path = version_dir / "02_5_story_bible.json"
        prompts_path = version_dir / "04_page_prompt.txt"
        story_bible_page_paths = [
            _story_bible_page_path(version_dir, page_number)
            for page_number in range(1, self.total_pages + 1)
        ]
        script_page_paths = [
            _script_page_path(version_dir, page_number)
            for page_number in range(1, self.total_pages + 1)
        ]
        styled_script_page_paths = [
            _styled_script_page_path(version_dir, page_number)
            for page_number in range(1, self.total_pages + 1)
        ]
        template_path = self._resolve_art_template(version_dir, episode_dir)
        prompt_template_paths = self._capture_prompt_templates_for_version(
            self._resolve_prompt_templates(),
            version_dir,
        )
        errors: list[str] = []
        error_details: list[str] = []

        if entities_path.exists():
            self._emit(
                PhaseSkipped(
                    phase="entities",
                    message="Skipped",
                    reason="checkpoint exists",
                )
            )
            entities = WorldStateCheckpoint.model_validate_json(
                entities_path.read_text(encoding="utf-8")
            )
        else:
            self._emit(PhaseStarted(phase="entities", message="Building entities from scraped notes..."))
            entities = build_entities_from_raw(
                raw_checkpoint_path=raw_path,
                output_path=entities_path,
                model_label="scraper-direct",
            )
            self._emit(PhaseCompleted(phase="entities", message="...done"))

        story_bible: StoryBibleCheckpoint | None = None
        if story_bible_path.exists():
            self._emit(
                PhaseSkipped(
                    phase="beater",
                    message="Skipped",
                    reason="checkpoint exists",
                )
            )
            story_bible = StoryBibleCheckpoint.model_validate_json(
                story_bible_path.read_text(encoding="utf-8")
            )
        else:
            scene_count = self.total_pages * self.panel_count
            self._emit(
                PhaseStarted(
                    phase="beater",
                    message="Creating story bible...",
                    details={"model": self.beater_model, "scene_count": scene_count},
                )
            )
            # Prepare and save prompts before model call.
            # The exact rendered strings are also the ones sent to the model.
            beater_system_prompt, beater_user_prompt = prepare_beater_prompts(
                version_dir=version_dir,
                content=raw.content,
                world=entities,
                scene_count=scene_count,
                raw_quotes=[
                    {"text": quote.text, "attribution": quote.attribution}
                    for quote in raw.quotes
                ],
                system_prompt_path=prompt_template_paths[MASTER_BEATER_SYSTEM_PROMPT_FILENAME],
                user_prompt_path=prompt_template_paths[MASTER_BEATER_USER_PROMPT_FILENAME],
            )
            try:
                story_bible = create_story_bible(
                    raw_checkpoint_path=raw_path,
                    entities_checkpoint_path=entities_path,
                    output_path=story_bible_path,
                    model=self.beater_model,
                    scene_count=scene_count,
                    system_prompt_text=beater_system_prompt,
                    user_prompt_text=beater_user_prompt,
                )
                if not story_bible_path.exists():
                    story_bible_path.write_text(
                        story_bible.model_dump_json(indent=2),
                        encoding="utf-8",
                    )
                self._emit(PhaseCompleted(phase="beater", message="...done"))
            except Exception as exc:
                errors.append(f"story_bible: {exc}")
                error_details.append(f"story_bible: {_format_exception_detail(exc)}")
                self._emit(
                    PhasePartialFailure(
                        phase="beater",
                        message="Story bible generation failed - skipping downstream phases",
                        skipped_phases=["script", "style", "prompt"],
                        error_detail=str(exc),
                    )
                )

        script_pages: list[ScriptCheckpoint] | None = None
        script_generated_this_run = False
        if story_bible is None:
            self._emit(
                PhaseSkipped(
                    phase="script",
                    message="Skipped",
                    reason="no story bible",
                )
            )
        elif all(path.exists() for path in script_page_paths):
            self._emit(
                PhaseSkipped(
                    phase="script",
                    message="Skipped",
                    reason="checkpoints exist",
                )
            )
            script_pages = _load_script_pages(script_page_paths)
        else:
            self._emit(
                PhaseStarted(
                    phase="script",
                    message="Writing script...",
                    details={"model": self.script_model},
                )
            )
            story_bible_pages: list[StoryBibleCheckpoint] = []
            try:
                story_bible_pages = write_story_bible_pages(
                    story_bible_checkpoint_path=story_bible_path,
                    output_paths=story_bible_page_paths,
                    total_pages=self.total_pages,
                )
            except Exception as exc:
                errors.append(f"script: {exc}")
                error_details.append(f"script: {_format_exception_detail(exc)}")
                self._emit(
                    PhasePartialFailure(
                        phase="script",
                        message="Story bible page splitting failed - skipping style and prompt phases",
                        skipped_phases=["style", "prompt"],
                        error_detail=str(exc),
                    )
                )
            if story_bible_pages:
                try:
                    generated_pages: list[ScriptCheckpoint] = []
                    for page_number, (story_bible_page, story_bible_page_path, script_page_path) in enumerate(
                        zip(story_bible_pages, story_bible_page_paths, script_page_paths),
                        start=1,
                    ):
                        script_system_prompt, script_user_prompt = prepare_scriptwriter_prompts(
                            version_dir=version_dir,
                            world=cast(WorldStateInput, entities),
                            story_bible=story_bible_page,
                            system_prompt_path=prompt_template_paths[SCRIPTWRITER_SYSTEM_PROMPT_FILENAME],
                            user_prompt_path=prompt_template_paths[SCRIPTWRITER_USER_PROMPT_FILENAME],
                            page_number=page_number,
                            output_suffix=f"page_{page_number:03d}",
                        )

                        generated_pages.append(
                            write_script(
                                raw_checkpoint_path=raw_path,
                                entities_checkpoint_path=entities_path,
                                story_bible_checkpoint_path=story_bible_page_path,
                                output_path=script_page_path,
                                model=self.script_model,
                                total_pages=1,
                                system_prompt_text=script_system_prompt,
                                user_prompt_text=script_user_prompt,
                            )
                        )

                    script_pages = apply_cross_page_continuity_errors(
                        renumber_script_page_checkpoints(generated_pages)
                    )
                    _write_script_pages(script_page_paths, script_pages)
                    script_generated_this_run = True
                    page_word = "page" if len(script_pages) == 1 else "pages"
                    self._emit(
                        PhaseCompleted(
                            phase="script",
                            message="...done",
                            details={"page_count": len(script_pages)},
                        )
                    )
                except Exception as exc:
                    errors.append(f"script: {exc}")
                    error_details.append(f"script: {_format_exception_detail(exc)}")
                    self._emit(
                        PhasePartialFailure(
                            phase="script",
                            message="Script generation failed - skipping style and prompt phases",
                            skipped_phases=["style", "prompt"],
                            error_detail=str(exc),
                        )
                    )

        if script_generated_this_run and script_pages is not None:
            for page_number, checkpoint in enumerate(script_pages, start=1):
                for generation_error in checkpoint.generation_errors:
                    error_prefix = "script" if len(script_pages) == 1 else f"script: page {page_number}"
                    errors.append(f"{error_prefix}: {generation_error}")
                    error_details.append(f"{error_prefix}: {generation_error}")
                    self._emit(
                        PhaseWarning(
                            phase="script",
                            message=f"Script validation warning (page {page_number})",
                            warning=generation_error,
                        )
                    )

        styled_script_pages: list[ScriptCheckpoint] | None = None
        if script_pages is None:
            self._emit(
                PhaseSkipped(
                    phase="style",
                    message="Skipped",
                    reason="no script",
                )
            )
        elif self.skip_style:
            self._emit(
                PhaseSkipped(
                    phase="style",
                    message="Skipped",
                    reason="--skip-style flag",
                )
            )
            styled_script_pages = script_pages
        elif all(path.exists() for path in styled_script_page_paths):
            self._emit(
                PhaseSkipped(
                    phase="style",
                    message="Skipped",
                    reason="checkpoints exist",
                )
            )
            styled_script_pages = _load_script_pages(styled_script_page_paths)
        else:
            template_path = self._capture_art_template_for_version(template_path, version_dir)
            self._emit(
                PhaseStarted(
                    phase="style",
                    message="Integrating art style...",
                    details={"model": self.style_model, "template": str(template_path)},
                )
            )
            try:
                art_template = _load_art_template(template_path)
                generated_styled_pages: list[ScriptCheckpoint] = []
                for page_number, (script_page, script_page_path, styled_script_page_path) in enumerate(
                    zip(script_pages, script_page_paths, styled_script_page_paths),
                    start=1,
                ):
                    style_system_prompt, style_user_prompt = prepare_style_integrator_prompts(
                        version_dir=version_dir,
                        script=script_page,
                        art_template=art_template,
                        system_prompt_path=prompt_template_paths[STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME],
                        user_prompt_path=prompt_template_paths[STYLE_INTEGRATOR_USER_PROMPT_FILENAME],
                        output_suffix=f"page_{page_number:03d}",
                    )

                    try:
                        generated_styled_pages.append(
                            integrate_style(
                                script_checkpoint_path=script_page_path,
                                art_style_template_path=template_path,
                                output_path=styled_script_page_path,
                                model=self.style_model,
                                system_prompt_text=style_system_prompt,
                                user_prompt_text=style_user_prompt,
                            )
                        )
                    except StyleIntegrationPartialFailure as exc:
                        generated_styled_pages.append(exc.checkpoint)
                        styled_script_page_path.write_text(
                            exc.checkpoint.model_dump_json(indent=2),
                            encoding="utf-8",
                        )
                        error_prefix = "style" if len(script_pages) == 1 else f"style: page {page_number}"
                        errors.append(f"{error_prefix}: {exc}")
                        error_details.append(f"{error_prefix}: {_format_exception_detail(exc)}")
                        self._emit(
                            PhaseWarning(
                                phase="style",
                                message=f"Style integration partially failed on page {page_number}",
                                warning=str(exc),
                            )
                        )

                styled_script_pages = generated_styled_pages
                page_word = "page" if len(styled_script_pages) == 1 else "pages"
                self._emit(
                    PhaseCompleted(
                        phase="style",
                        message="...done",
                        details={"page_count": len(styled_script_pages)},
                    )
                )
            except Exception as exc:
                errors.append(f"style: {exc}")
                error_details.append(f"style: {_format_exception_detail(exc)}")
                self._emit(
                    PhasePartialFailure(
                        phase="style",
                        message="Style integration failed - skipping prompt phase",
                        skipped_phases=["prompt"],
                        error_detail=str(exc),
                    )
                )

        page_prompt: str | None = None
        page_prompts: list[tuple[Path, str]] = []
        prompt_script_pages = script_pages if self.skip_style else styled_script_pages
        if not prompt_script_pages:
            self._emit(
                PhaseSkipped(
                    phase="prompt",
                    message="Skipped",
                    reason="no script available",
                )
            )
        else:
            template_path = self._capture_art_template_for_version(template_path, version_dir)
            prompt_script_paths = script_page_paths if self.skip_style else styled_script_page_paths
            expected_prompt_paths = [
                version_dir / f"04_page_{page_number}_prompt.txt"
                for page_number in range(1, self.total_pages + 1)
            ]

            if all(path.exists() for path in expected_prompt_paths):
                self._emit(
                    PhaseSkipped(
                        phase="prompt",
                        message="Skipped",
                        reason="checkpoints exist",
                    )
                )
                page_prompt = expected_prompt_paths[0].read_text(encoding="utf-8")
            else:
                self._emit(
                    PhaseStarted(
                        phase="prompt",
                        message="Generating page prompt...",
                        details={"template": str(template_path), "page_count": len(prompt_script_pages)},
                    )
                )
                try:
                    art_template = _load_art_template(template_path)
                    for page_number, (prompt_script, prompt_script_path) in enumerate(
                        zip(prompt_script_pages, prompt_script_paths),
                        start=1,
                    ):
                        try:
                            prompt_text = prepare_page_prompt_template(
                                version_dir=version_dir,
                                world=entities,
                                script=prompt_script,
                                art_template=art_template,
                                template_path=prompt_template_paths[PAGE_PROMPT_TEMPLATE_FILENAME],
                                output_suffix=f"page_{page_number:03d}",
                            )
                        except Exception as exc:
                            self._emit(
                                PhaseWarning(
                                    phase="prompt",
                                    message=f"Failed to save interpolated page {page_number} prompt template",
                                    warning=str(exc),
                                )
                            )
                            continue

                        page_output_path = version_dir / f"04_page_{page_number}_prompt.txt"
                        page_output_path.write_text(prompt_text, encoding="utf-8")
                        page_prompts.append((page_output_path, prompt_text))

                    if page_prompts:
                        page_prompt = page_prompts[0][1]  # First page's prompt for backward compat
                    page_word = "page" if len(page_prompts) == 1 else "pages"
                    self._emit(
                        PhaseCompleted(
                            phase="prompt",
                            message="...done",
                            details={"page_count": len(page_prompts)},
                        )
                    )
                except Exception as exc:
                    errors.append(f"page_prompt: {exc}")
                    error_details.append(f"page_prompt: {_format_exception_detail(exc)}")
                    self._emit(
                        PhaseError(
                            phase="prompt",
                            message="Page prompt generation failed",
                            error=str(exc),
                            exception=exc,
                        )
                    )

        # Determine final status
        checkpoint_keys = (
            "entities",
            "story_bible",
            "script",
            "styled_script",
            "page_prompt",
        )
        checkpoints_created = []
        if entities_path.exists():
            checkpoints_created.append("entities")
        if story_bible_path.exists():
            checkpoints_created.append("story_bible")
        if all(path.exists() for path in script_page_paths):
            checkpoints_created.append("script")
        if all(path.exists() for path in styled_script_page_paths) and not self.skip_style:
            checkpoints_created.append("styled_script")
        if all(path.exists() for path in expected_prompt_paths) if prompt_script_pages else False:
            checkpoints_created.append("page_prompt")

        failed_phases = []
        if story_bible is None:
            failed_phases.append("beater")
        if script_pages is None and story_bible is not None:
            failed_phases.append("script")
        if styled_script_pages is None and script_pages is not None and not self.skip_style:
            failed_phases.append("style")
        if page_prompt is None and prompt_script_pages:
            failed_phases.append("prompt")

        final_status = "ok" if not errors else ("partial" if script_pages is not None else "failed")
        self._emit(
            RunCompleted(
                status=final_status,
                version=version_name,
                version_dir=str(version_dir),
                checkpoints=checkpoints_created,
                failed_phases=failed_phases,
                error_messages=errors,
            )
        )

        return {
            "raw_text": raw.model_dump(),
            "entities": entities.model_dump(),
            "story_bible": story_bible.model_dump() if story_bible is not None else None,
            "script": [checkpoint.model_dump() for checkpoint in script_pages] if script_pages is not None else None,
            "styled_script": [checkpoint.model_dump() for checkpoint in styled_script_pages] if styled_script_pages is not None else None,
            "page_prompt": {
                "output_path": str(prompts_path),
                "prompt": page_prompt,
            } if page_prompt is not None else None,
            "errors": errors,
            "error_details": error_details,
            "version": version_name,
            "version_dir": str(version_dir),
        }


# ---------------------------------------------------------------------------
# CLI Event Printer (adapts events back to terminal output)
# ---------------------------------------------------------------------------


def _format_event_for_cli(event: PipelineEventUnion) -> str:
    """Format a pipeline event as a human-readable CLI message."""
    if isinstance(event, PhaseStarted):
        msg = f"[5/5] {event.message}"  # Will be corrected by phase name below
        if event.phase == "scrape":
            msg = f"[1/5] {event.message}"
        elif event.phase == "entities":
            msg = f"[2/5] {event.message}"
        elif event.phase == "beater":
            msg = f"[3/5] {event.message}"
        elif event.phase == "script":
            msg = f"[4/5] {event.message}"
        elif event.phase == "style":
            msg = f"[4.5/5] {event.message}"
        elif event.phase == "prompt":
            msg = f"[5/5] {event.message}"
        if event.details:
            detail_str = ", ".join(f"{k}: {v}" for k, v in event.details.items())
            msg += f"  ({detail_str})"
        return msg

    elif isinstance(event, PhaseSkipped):
        msg = f"[5/5] {event.message} ({event.reason})"
        if event.phase == "scrape":
            msg = f"[1/5] Scraping...skipped ({event.reason})"
        elif event.phase == "entities":
            msg = f"[2/5] Building entities...skipped ({event.reason})"
        elif event.phase == "beater":
            msg = f"[3/5] Creating story bible...skipped ({event.reason})"
        elif event.phase == "script":
            msg = f"[4/5] Writing script...skipped ({event.reason})"
        elif event.phase == "style":
            msg = f"[4.5/5] Integrating art style...skipped ({event.reason})"
        elif event.phase == "prompt":
            msg = f"[5/5] Generating page prompt...skipped ({event.reason})"
        return msg

    elif isinstance(event, PhaseCompleted):
        msg = "      ...done"
        if event.details:
            detail_str = ", ".join(f"{v}" for v in event.details.values())
            msg += f" ({detail_str})"
        return msg

    elif isinstance(event, PhaseWarning):
        return f"      ...WARN {event.warning}"

    elif isinstance(event, PhaseError):
        return f"      ...ERROR {event.error}"

    elif isinstance(event, PhasePartialFailure):
        skipped = ", ".join(event.skipped_phases) if event.skipped_phases else "none"
        return f"      ...ERROR ({event.error_detail} — skipping phases {skipped})"

    elif isinstance(event, VersionCreated):
        return f"      episode: {event.episode_slug}/{event.version}"

    elif isinstance(event, RunCompleted):
        failed_str = ", ".join(event.failed_phases) if event.failed_phases else "none"
        return f"Run completed: status={event.status}, checkpoints={len(event.checkpoints)}, failed_phases={failed_str}"

    return f"[{event.__class__.__name__}]"


def _print_event_callback(event: PipelineEventUnion) -> None:
    """Callback that prints pipeline events in the original CLI format."""
    msg = _format_event_for_cli(event)
    if msg:
        print(msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Run the campaign-aware comic pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # First run for a campaign:\n"
            "  python src/pipeline.py dreadmarsh https://scrybequill.com/share/...\n\n"
            "  # Re-run style integration and prompt generation with the same script:\n"
            "  python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from style\n\n"
            "  # Re-run only the final page prompt from the styled script checkpoint:\n"
            "  python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from prompt\n\n"
            "  # Fix source text errors (rerun everything from scrape):\n"
            "  python src/pipeline.py dreadmarsh https://scrybequill.com/share/... --rerun-from scrape\n"
        ),
    )
    parser.add_argument("campaign", help="Campaign name (e.g. dreadmarsh, belowdown)")
    parser.add_argument("url", help="ScrybeQuill story URL")
    parser.add_argument(
        "--campaigns-root",
        default=str(CAMPAIGNS_ROOT),
        help="Root directory for all campaign data (default: campaigns/)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model name used for all stages (default: %(default)s). Override per-stage with --beater-model, --script-model, --style-model.",
    )
    parser.add_argument(
        "--beater-model",
        default=None,
        help="Model name for Phase 3 story bible creation (overrides --model)",
    )
    parser.add_argument(
        "--script-model",
        default=None,
        help="Model name for Phase 4 scripting (overrides --model)",
    )
    parser.add_argument(
        "--style-model",
        default=None,
        help="Model name for Phase 4.5 art style integration (overrides --model)",
    )
    parser.add_argument(
        "--panel-count",
        default=6,
        type=int,
        help="Target number of scenes to generate in Phase 3",
    )
    parser.add_argument(
        "--total-pages",
        default=1,
        type=int,
        help="Total number of pages to generate (default: 1). Scenes are distributed evenly across pages.",
    )
    parser.add_argument(
        "--art-style-template",
        default=None,
        help=(
            "Explicit path to an art direction template JSON file. "
            f"If omitted, the pipeline looks for campaigns/<campaign>/{ART_DIRECTION_TEMPLATE_FILENAME}"
        ),
    )
    parser.add_argument(
        "--master-beater-system-prompt",
        default=None,
        help=(
            "Explicit path to the master beater system prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{MASTER_BEATER_SYSTEM_PROMPT_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--master-beater-user-prompt",
        default=None,
        help=(
            "Explicit path to the master beater user prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{MASTER_BEATER_USER_PROMPT_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--scriptwriter-system-prompt",
        default=None,
        help=(
            "Explicit path to the scriptwriter system prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{SCRIPTWRITER_SYSTEM_PROMPT_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--scriptwriter-user-prompt",
        default=None,
        help=(
            "Explicit path to the scriptwriter user prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{SCRIPTWRITER_USER_PROMPT_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--style-integrator-system-prompt",
        default=None,
        help=(
            "Explicit path to the style integrator system prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--style-integrator-user-prompt",
        default=None,
        help=(
            "Explicit path to the style integrator user prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{STYLE_INTEGRATOR_USER_PROMPT_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--page-prompt-template",
        default=None,
        help=(
            "Explicit path to the page prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{PAGE_PROMPT_TEMPLATE_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--rerun-from",
        choices=["scrape", "entities", "beater", "script", "style", "prompt"],
        default=None,
        help=(
            "Invalidate checkpoints from this phase onward and rerun. "
            "Prior phases are cloned from the last version. "
            "Options: scrape, entities, beater, script, style, prompt"
        ),
    )
    parser.add_argument(
        "--recap-version",
        choices=["short", "standard", "alternate", "alt", "long"],
        default="standard",
        help=(
            "Recap variant to use as raw content (captured on initial scrape and reused later). "
            "Options: short, standard, alternate/alt, long"
        ),
    )
    parser.add_argument(
        "--skip-style",
        action="store_true",
        help=(
            "Skip Phase 3.5 style integration and generate the page prompt directly "
            "from 03_script.json."
        ),
    )

    args = parser.parse_args()
    rerun_from_arg = args.rerun_from

    pipeline = ComicPipeline(
        url=args.url,
        campaign=args.campaign,
        campaigns_root=Path(args.campaigns_root),
        beater_model=args.beater_model or args.model,
        script_model=args.script_model or args.model,
        style_model=args.style_model or args.model,
        panel_count=args.panel_count,
        total_pages=args.total_pages,
        art_style_template=Path(args.art_style_template) if args.art_style_template else None,
        master_beater_system_prompt=Path(args.master_beater_system_prompt)
        if args.master_beater_system_prompt
        else None,
        master_beater_user_prompt=Path(args.master_beater_user_prompt)
        if args.master_beater_user_prompt
        else None,
        scriptwriter_system_prompt=Path(args.scriptwriter_system_prompt)
        if args.scriptwriter_system_prompt
        else None,
        scriptwriter_user_prompt=Path(args.scriptwriter_user_prompt)
        if args.scriptwriter_user_prompt
        else None,
        style_integrator_system_prompt=Path(args.style_integrator_system_prompt)
        if args.style_integrator_system_prompt
        else None,
        style_integrator_user_prompt=Path(args.style_integrator_user_prompt)
        if args.style_integrator_user_prompt
        else None,
        page_prompt_template=Path(args.page_prompt_template)
        if args.page_prompt_template
        else None,
        rerun_from=rerun_from_arg,
        recap_version=args.recap_version,
        skip_style=args.skip_style,
        event_callback=_print_event_callback,
    )
    try:
        result = await pipeline.run()
    except Exception as exc:
        full_detail = _format_exception_detail(exc)
        status_blob = {
            "status": "failed",
            "campaign": args.campaign,
            "errors": [str(exc)],
            "error_details": [full_detail],
        }
        status_json = json.dumps(status_blob, indent=2)
        print(status_json)
        if pipeline._version_dir is not None:
            (pipeline._version_dir / "run_status.json").write_text(status_json, encoding="utf-8")
        raise

    checkpoint_keys = (
        "entities",
        "story_bible",
        "script",
        "styled_script",
        "page_prompt",
    )
    failed = [key for key in checkpoint_keys if result.get(key) is None]
    status_blob = {
        "status": "partial" if failed else "ok",
        "campaign": args.campaign,
        "version": result["version"],
        "version_dir": result["version_dir"],
        "checkpoints": [key for key in checkpoint_keys if result.get(key) is not None],
        "failed": failed,
        "errors": result.get("errors", []),
        "error_details": result.get("error_details", []),
    }
    status_json = json.dumps(status_blob, indent=2)
    print(status_json)
    run_status_path = Path(cast(str, result["version_dir"])) / "run_status.json"
    run_status_path.write_text(status_json, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(_run_cli())
