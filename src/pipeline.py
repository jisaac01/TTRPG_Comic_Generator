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
    EPISODE_ENTITIES_FILENAME,
    WorldStateCheckpoint,
    build_entities_from_raw,
    write_entities_bible,
    write_episode_entities,
)
from image_generator import ImageGenerator
from image_stitcher import stitch_panel_images
from model_defaults import DEFAULT_MODEL
from pipeline_config import (
    CAMPAIGNS_ROOT,
    RerunFrom,
    RunConfig,
    effective_rerun_from,
    should_copy_prompt_artifacts,
    should_run_stage,
)
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
from art_styles import (
    ART_DIRECTION_TEMPLATE_FILENAME,
    default_art_style,
    list_art_styles,
    resolve_art_style,
)
from prompter import (
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
    ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME,
    ENTITIES_CONTINUITY_USER_PROMPT_FILENAME,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    PROMPT_TEMPLATE_FILENAMES,
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)
from scriptwriter import WorldStateInput, Page
from style_integrator import StyleIntegrationPartialFailure, integrate_style
from scraper import RawTextCheckpoint, normalize_recap_version, scrape_scrybequill
from scriptwriter import (
    ScriptCheckpoint,
    apply_cross_page_continuity_errors,
    build_story_bible_panel_units,
    merge_panel_scripts_into_page,
    renumber_script_page_checkpoints,
    write_script,
    write_story_bible_pages,
    write_story_bible_panels,
)
from master_beater import StoryBibleCheckpoint, create_story_bible

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_FILENAME = "index.json"
EPISODE_META_FILENAME = "episode_meta.json"
RUN_STATUS_FILENAME = "run_status.json"
WORKING_DIR_NAME = "working"
PROMPTS_SUBDIR_NAME = "prompts"
STORY_BIBLE_PAGE_GLOB = "02_6_story_bible_page_*.json"
STORY_BIBLE_PANEL_GLOB = "02_6_story_bible_page_*_panel_*.json"
SCRIPT_PAGE_GLOB = "03_script_page_*.json"
SCRIPT_PANEL_GLOB = "03_script_page_*_panel_*.json"
STYLED_SCRIPT_PAGE_GLOB = "03_5_styled_script_page_*.json"
PAGE_PROMPT_GLOB = "04_page_*_prompt.txt"

# RerunFrom is imported from pipeline_config above


def _story_bible_page_path(version_dir: Path, page_number: int) -> Path:
    return version_dir / f"02_6_story_bible_page_{page_number:03d}.json"


def _story_bible_panel_path(version_dir: Path, page_number: int, panel_index: int) -> Path:
    return version_dir / f"02_6_story_bible_page_{page_number:03d}_panel_{panel_index:03d}.json"


def _script_page_path(version_dir: Path, page_number: int) -> Path:
    return version_dir / f"03_script_page_{page_number:03d}.json"


def _script_panel_path(version_dir: Path, page_number: int, panel_index: int) -> Path:
    return version_dir / f"03_script_page_{page_number:03d}_panel_{panel_index:03d}.json"


def _styled_script_page_path(version_dir: Path, page_number: int) -> Path:
    return version_dir / f"03_5_styled_script_page_{page_number:03d}.json"


def _panel_prompt_path(version_dir: Path, page_number: int, panel_index: int) -> Path:
    return version_dir / f"04_page_{page_number}_panel_{panel_index}_prompt.txt"


def _copy_checkpoint_patterns(prev_dir: Path, version_dir: Path, patterns: list[str]) -> None:
    for pattern in patterns:
        if "*" in pattern:
            for src in prev_dir.glob(pattern):
                shutil.copy2(src, version_dir / src.name)
            continue

        src = prev_dir / pattern
        if src.exists():
            shutil.copy2(src, version_dir / src.name)


def _working_dir(episode_dir: Path) -> Path:
    return episode_dir / WORKING_DIR_NAME


def _list_version_dirs(episode_dir: Path) -> list[Path]:
    if not episode_dir.exists():
        return []
    return sorted(
        (
            path
            for path in episode_dir.iterdir()
            if path.is_dir() and re.fullmatch(r"v\d{3}", path.name)
        ),
        key=lambda path: int(path.name[1:]),
    )


def _latest_version_dir(episode_dir: Path) -> Path | None:
    versions = _list_version_dirs(episode_dir)
    return versions[-1] if versions else None


def _copy_tree_contents(src: Path, dest: Path) -> None:
    """Copy all files and subdirectories from src into dest (overwrite dest children)."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _ensure_working_dir(episode_dir: Path) -> Path:
    """
    Ensure episode/working exists.

    If working is missing or empty and a historical version exists, seed working
    from the latest version (migration path for pre-working campaigns).
    """
    working = _working_dir(episode_dir)
    working.mkdir(parents=True, exist_ok=True)
    if not any(working.iterdir()):
        latest = _latest_version_dir(episode_dir)
        if latest is not None:
            _copy_tree_contents(latest, working)
    return working


def _mirror_path_to_working(
    version_path: Path,
    version_dir: Path,
    working_dir: Path,
) -> None:
    """Copy a version-dir path into working at the same relative location."""
    if not version_path.exists():
        return
    try:
        relative = version_path.relative_to(version_dir)
    except ValueError:
        return
    dest = working_dir / relative
    if version_path.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(version_path, dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(version_path, dest)


def _sync_version_to_working(version_dir: Path, working_dir: Path) -> None:
    """Overwrite working files with every file currently present in the version dir."""
    working_dir.mkdir(parents=True, exist_ok=True)
    for path in version_dir.rglob("*"):
        if path.is_file():
            _mirror_path_to_working(path, version_dir, working_dir)


def _dual_unlink(version_path: Path, version_dir: Path, working_dir: Path) -> None:
    version_path.unlink(missing_ok=True)
    try:
        relative = version_path.relative_to(version_dir)
    except ValueError:
        return
    (working_dir / relative).unlink(missing_ok=True)


def _dual_delete_matching(version_dir: Path, working_dir: Path, pattern: str) -> None:
    for path in list(version_dir.glob(pattern)):
        _dual_unlink(path, version_dir, working_dir)


def _dual_rmtree(version_path: Path, version_dir: Path, working_dir: Path) -> None:
    if version_path.exists():
        shutil.rmtree(version_path, ignore_errors=True)
    try:
        relative = version_path.relative_to(version_dir)
    except ValueError:
        return
    working_target = working_dir / relative
    if working_target.exists():
        shutil.rmtree(working_target, ignore_errors=True)


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


def _prompt_template_mismatch_detail(exc: BaseException) -> str | None:
    """Return a user-facing detail when an exception is a prompt template mismatch."""
    detail = str(exc).strip()
    if detail.startswith("Prompt template variable mismatch in "):
        return detail
    return None

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


def _read_episode_meta(episode_dir: Path) -> dict:
    meta_path = episode_dir / EPISODE_META_FILENAME
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_episode_meta(episode_dir: Path, meta: dict) -> None:
    episode_dir.mkdir(parents=True, exist_ok=True)
    meta_path = episode_dir / EPISODE_META_FILENAME
    fd, tmp_name = tempfile.mkstemp(dir=episode_dir, prefix=".episode_meta_", suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)
        Path(tmp_name).replace(meta_path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _read_run_config(version_dir: Path) -> dict | None:
    status_path = version_dir / RUN_STATUS_FILENAME
    if not status_path.exists():
        return None
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    run_config = payload.get("run_config")
    return run_config if isinstance(run_config, dict) else None


_PRESERVE_PATTERNS_BY_STAGE: dict[RerunFrom | None, list[str]] = {
    "scrape": [],
    "entities": ["01_raw_text.json"],
    "beater": ["01_raw_text.json", "02_entities.json"],
    "script": ["01_raw_text.json", "02_entities.json", "02_5_story_bible.json"],
    "style": [
        "01_raw_text.json",
        "02_entities.json",
        "02_5_story_bible.json",
        STORY_BIBLE_PAGE_GLOB,
        STORY_BIBLE_PANEL_GLOB,
        SCRIPT_PAGE_GLOB,
        SCRIPT_PANEL_GLOB,
    ],
    "prompt": [
        "01_raw_text.json",
        "02_entities.json",
        "02_5_story_bible.json",
        STORY_BIBLE_PAGE_GLOB,
        STORY_BIBLE_PANEL_GLOB,
        SCRIPT_PAGE_GLOB,
        SCRIPT_PANEL_GLOB,
        STYLED_SCRIPT_PAGE_GLOB,
    ],
    None: [
        "01_raw_text.json",
        "02_entities.json",
        "02_5_story_bible.json",
        STORY_BIBLE_PAGE_GLOB,
        STORY_BIBLE_PANEL_GLOB,
        SCRIPT_PAGE_GLOB,
        SCRIPT_PANEL_GLOB,
        STYLED_SCRIPT_PAGE_GLOB,
    ],
}


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
    episode_dir: Path,
    rerun_from: RerunFrom | None,
    new_config: dict | None = None,
) -> tuple[Path, str, RerunFrom | None]:
    """
    Create the next version directory.

    Ensures episode/working exists (seeding from the latest version when needed),
    then copies only checkpoints preserved by the effective rerun stage from
    working into the new version directory.

    Returns (version_dir, version_name, effective_rerun_from).
    """
    working = _ensure_working_dir(episode_dir)
    version_name = _next_version_name(episode_dir)
    version_dir = episode_dir / version_name
    version_dir.mkdir(parents=True, exist_ok=True)

    effective_rerun = rerun_from
    if any(working.iterdir()):
        prev_config = _read_run_config(working)
        effective_rerun = effective_rerun_from(
            rerun_from,
            prev_config,
            new_config or {},
        )
        files_to_copy = _PRESERVE_PATTERNS_BY_STAGE.get(
            effective_rerun,
            _PRESERVE_PATTERNS_BY_STAGE[None],
        )
        _copy_checkpoint_patterns(working, version_dir, files_to_copy)

        if should_copy_prompt_artifacts(effective_rerun, prev_config, new_config or {}):
            for prev_prompt_file in working.glob(PAGE_PROMPT_GLOB):
                shutil.copy2(prev_prompt_file, version_dir / prev_prompt_file.name)

            prev_prompts_dir = working / PROMPTS_SUBDIR_NAME
            if prev_prompts_dir.exists():
                dest_prompts = version_dir / PROMPTS_SUBDIR_NAME
                if dest_prompts.exists():
                    shutil.rmtree(dest_prompts)
                shutil.copytree(prev_prompts_dir, dest_prompts)

    return version_dir, version_name, effective_rerun


def _generate_script_pages_panel_mode(
    *,
    version_dir: Path,
    story_bible_path: Path,
    raw_path: Path,
    episode_entities_path: Path,
    episode_entities: WorldStateInput,
    total_pages: int,
    script_model: str,
    prompt_template_paths: dict[str, Path],
) -> list[ScriptCheckpoint]:
    story_bible = StoryBibleCheckpoint.model_validate_json(
        story_bible_path.read_text(encoding="utf-8")
    )
    units = build_story_bible_panel_units(story_bible, total_pages)
    panel_story_paths = {
        (unit.page_number, unit.panel_index): _story_bible_panel_path(
            version_dir, unit.page_number, unit.panel_index
        )
        for unit in units
    }
    write_story_bible_panels(
        story_bible_checkpoint_path=story_bible_path,
        output_paths=panel_story_paths,
        total_pages=total_pages,
    )

    panel_scripts: dict[tuple[int, int], ScriptCheckpoint] = {}
    for unit in units:
        key = (unit.page_number, unit.panel_index)
        story_path = panel_story_paths[key]
        script_path = _script_panel_path(version_dir, unit.page_number, unit.panel_index)
        script_system_prompt, script_user_prompt = prepare_scriptwriter_prompts(
            version_dir=version_dir,
            world=episode_entities,
            story_bible=unit.checkpoint,
            system_prompt_path=prompt_template_paths[SCRIPTWRITER_SYSTEM_PROMPT_FILENAME],
            user_prompt_path=prompt_template_paths[SCRIPTWRITER_USER_PROMPT_FILENAME],
            page_number=unit.page_number,
            output_suffix=f"page_{unit.page_number:03d}_panel_{unit.panel_index:03d}",
        )
        panel_scripts[key] = write_script(
            raw_checkpoint_path=raw_path,
            entities_checkpoint_path=episode_entities_path,
            story_bible_checkpoint_path=story_path,
            output_path=script_path,
            model=script_model,
            total_pages=1,
            system_prompt_text=script_system_prompt,
            user_prompt_text=script_user_prompt,
        )

    merged_pages: list[ScriptCheckpoint] = []
    for page_number in range(1, total_pages + 1):
        page_panel_scripts = [
            panel_scripts[(unit.page_number, unit.panel_index)]
            for unit in units
            if unit.page_number == page_number
        ]
        merged_pages.append(
            merge_panel_scripts_into_page(page_panel_scripts, page_number)
        )

    return apply_cross_page_continuity_errors(
        renumber_script_page_checkpoints(merged_pages)
    )


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
        aspect_ratio: str = "3:2",
        generation_mode: Literal["page", "panel"] = "page",
        art_style_template: Path | None = None,
        art_style: str | None = None,
        master_beater_system_prompt: Path | None = None,
        master_beater_user_prompt: Path | None = None,
        scriptwriter_system_prompt: Path | None = None,
        scriptwriter_user_prompt: Path | None = None,
        style_integrator_system_prompt: Path | None = None,
        style_integrator_user_prompt: Path | None = None,
        page_prompt_template: Path | None = None,
        rerun_from: RerunFrom | None = None,
        stop_after: RerunFrom | None = None,
        recap_version: str = "standard",
        skip_style: bool = False,
        generate_images: bool = False,
        image_generation_model: str = "gemini-2.5-flash-image",
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
        self.aspect_ratio = aspect_ratio
        self.generation_mode = generation_mode
        self.art_style_template = art_style_template
        self.art_style = art_style
        self.master_beater_system_prompt = master_beater_system_prompt
        self.master_beater_user_prompt = master_beater_user_prompt
        self.scriptwriter_system_prompt = scriptwriter_system_prompt
        self.scriptwriter_user_prompt = scriptwriter_user_prompt
        self.style_integrator_system_prompt = style_integrator_system_prompt
        self.style_integrator_user_prompt = style_integrator_user_prompt
        self.page_prompt_template = page_prompt_template
        self.rerun_from: RerunFrom | None = rerun_from
        self.stop_after: RerunFrom | None = stop_after
        self.recap_version = normalize_recap_version(recap_version)
        self.skip_style = skip_style
        self.generate_images = generate_images
        self.image_generation_model = image_generation_model
        self.event_callback = event_callback or (lambda _: None)
        self._version_dir: Path | None = None
        self._working_dir: Path | None = None
        self._effective_rerun_from: RerunFrom | None = rerun_from

    def run_config_dict(self) -> dict:
        config = {
            "panel_count": self.panel_count,
            "total_pages": self.total_pages,
            "recap_version": self.recap_version,
            "aspect_ratio": self.aspect_ratio,
            "generation_mode": self.generation_mode,
            "art_style": self.art_style,
            "skip_style": self.skip_style,
            "generate_images": self.generate_images,
            "rerun_from": self._effective_rerun_from or self.rerun_from,
            "stop_after": self.stop_after,
        }
        return config

    def _should_run_stage(self, stage: RerunFrom) -> bool:
        return should_run_stage(stage, self.stop_after)

    def _emit(self, event: PipelineEventUnion) -> None:
        """Emit an event via the callback."""
        self.event_callback(event)

    def _emit_prompt_template_mismatch_warning(self, phase: Literal["beater", "script", "style", "prompt"], exc: BaseException) -> None:
        detail = _prompt_template_mismatch_detail(exc)
        if detail is None:
            return
        self._emit(
            PhaseWarning(
                phase=phase,
                message="Prompt template update required",
                warning=detail,
            )
        )

    def _run_image_generation_stage(self, version_dir: Path) -> tuple[list[str], list[str]]:
        """Generate PNG images from each saved page prompt file."""
        prompt_paths = sorted(version_dir.glob("04_page_*_prompt.txt"))
        if not prompt_paths:
            return [], []

        generator = ImageGenerator(model=self.image_generation_model)
        generated_paths: list[str] = []
        errors: list[str] = []

        for prompt_path in prompt_paths:
            page_number_match = re.search(r"04_page_(\d+)(?:_panel_(\d+))?_prompt\.txt$", prompt_path.name)
            if page_number_match is None:
                errors.append(f"image_generation: unable to determine page number for {prompt_path.name}")
                continue

            page_number = page_number_match.group(1)
            panel_number = page_number_match.group(2)
            output_path = version_dir / (
                f"05_page_{page_number}_panel_{panel_number}.png"
                if panel_number is not None
                else f"05_page_{page_number}.png"
            )
            try:
                prompt_text = prompt_path.read_text(encoding="utf-8")
                image_bytes = generator.generate_image(prompt_text)
                generator.save_image(image_bytes, output_path)
                generated_paths.append(str(output_path))
            except Exception as exc:
                errors.append(f"image_generation: page {page_number}: {exc}")
                self._emit(
                    PhaseWarning(
                        phase="image_generation",
                        message=f"Image generation failed for page {page_number}",
                        warning=str(exc),
                    )
                )

        return generated_paths, errors

    def _run_stitching_stage(self, version_dir: Path) -> tuple[list[str], list[str]]:
        """Stitch generated panel images into final page PNGs for panel-mode output."""
        if self.generation_mode != "panel":
            return [], []

        panel_paths = sorted(version_dir.glob("05_page_*_panel_*.png"))
        if not panel_paths:
            return [], []

        stitched_paths: list[str] = []
        errors: list[str] = []

        pages = sorted({int(re.search(r"05_page_(\d+)_panel_\d+\.png$", path.name).group(1)) for path in panel_paths})
        for page_number in pages:
            page_paths = sorted(version_dir.glob(f"05_page_{page_number}_panel_*.png"))
            output_path = version_dir / f"06_page_{page_number}.png"
            try:
                stitch_panel_images(
                    page_paths,
                    output_path,
                    aspect_ratio=self.aspect_ratio,
                )
                stitched_paths.append(str(output_path))
            except Exception as exc:
                errors.append(f"stitching: page {page_number}: {exc}")
                self._emit(
                    PhaseWarning(
                        phase="stitching",
                        message=f"Stitching failed for page {page_number}",
                        warning=str(exc),
                    )
                )

        return stitched_paths, errors

    def _apply_recap_selection(self, raw: RawTextCheckpoint) -> tuple[RawTextCheckpoint, bool, bool]:
        """Select content from cached recap variants and report selection/content changes.

        Raises ValueError when the requested variant is missing from the scrape
        checkpoint. Does not re-scrape; re-run from Scrape to refresh variants.
        """
        selected = self.recap_version
        variants = raw.recap_variants or {}
        chosen = variants.get(selected)
        if not chosen or not str(chosen).strip():
            available = sorted(k for k, v in variants.items() if v and str(v).strip())
            available_text = ", ".join(repr(k) for k in available) if available else "(none)"
            raise ValueError(
                f"Recap variant {selected!r} is not available in the cached scrape. "
                f"Available: {available_text}. "
                "Re-run from Scrape to refresh recap variants."
            )

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

    def _resolve_selected_art_style_id(self) -> str | None:
        """Return the art style id to persist for this run (if resolvable)."""
        if self.art_style is not None:
            return self.art_style
        if self.art_style_template is not None:
            return None
        try:
            return default_art_style(self.campaigns_root, self.campaign).id
        except FileNotFoundError:
            return None

    def _resolve_art_template(self, version_dir: Path, episode_dir: Path) -> Path:
        """Resolve art style template: explicit path > art_style id > default style."""
        if self.art_style_template is not None:
            return self.art_style_template
        if self.art_style is not None:
            return resolve_art_style(
                self.campaigns_root, self.campaign, self.art_style
            ).path
        try:
            return default_art_style(self.campaigns_root, self.campaign).path
        except FileNotFoundError:
            # Fall back to a template in the version dir if one was cloned from a prior version.
            version_template = version_dir / ART_DIRECTION_TEMPLATE_FILENAME
            if version_template.exists():
                return version_template
            if DEFAULT_ART_DIRECTION_TEMPLATE_PATH.exists():
                return DEFAULT_ART_DIRECTION_TEMPLATE_PATH
            raise

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
            ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME: self._campaign_prompt_path(
                ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME
            ),
            ENTITIES_CONTINUITY_USER_PROMPT_FILENAME: self._campaign_prompt_path(
                ENTITIES_CONTINUITY_USER_PROMPT_FILENAME
            ),
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

    async def run(self) -> dict[str, object]:
        self._version_dir = None
        self._working_dir = None
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
            working_dir = _ensure_working_dir(episode_dir)
            version_dir, version_name, effective_rerun = _create_version_dir(
                episode_dir,
                self.rerun_from,
                self.run_config_dict(),
            )
            self._effective_rerun_from = effective_rerun
            self._version_dir = version_dir
            self._working_dir = working_dir
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
            working_dir = _ensure_working_dir(episode_dir)
            version_dir, version_name, effective_rerun = _create_version_dir(
                episode_dir,
                self.rerun_from,
                self.run_config_dict(),
            )
            self._effective_rerun_from = effective_rerun
            self._version_dir = version_dir
            self._working_dir = working_dir
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
                    # Recap body feeds the story bible, not keyed entities.
                    # Keep 02_entities.json; invalidate beater outputs and below.
                    _dual_unlink(version_dir / "02_5_story_bible.json", version_dir, working_dir)
                    _dual_delete_matching(version_dir, working_dir, STORY_BIBLE_PAGE_GLOB)
                    _dual_delete_matching(version_dir, working_dir, STORY_BIBLE_PANEL_GLOB)
                    _dual_delete_matching(version_dir, working_dir, SCRIPT_PAGE_GLOB)
                    _dual_delete_matching(version_dir, working_dir, SCRIPT_PANEL_GLOB)
                    _dual_delete_matching(version_dir, working_dir, STYLED_SCRIPT_PAGE_GLOB)
                    _dual_delete_matching(version_dir, working_dir, "04_page_*.txt")
                    _dual_rmtree(version_dir / PROMPTS_SUBDIR_NAME, version_dir, working_dir)
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

        # Persist resolved art style id when not already set (for run_status).
        if self.art_style is None and self.art_style_template is None:
            self.art_style = self._resolve_selected_art_style_id()
        self._ensure_campaign_prompt_templates()
        entities_path = version_dir / "02_entities.json"
        story_bible_path = version_dir / "02_5_story_bible.json"
        prompts_path = version_dir / "04_page_1_prompt.txt"
        if self.generation_mode == "panel":
            prompts_path = _panel_prompt_path(version_dir, 1, 1)
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

        entities: WorldStateCheckpoint | None = None
        episode_entities_path = version_dir / EPISODE_ENTITIES_FILENAME
        episode_entities: WorldStateCheckpoint | None = None

        if self._should_run_stage("entities"):
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
                if not entities_path.exists():
                    entities_path.write_text(
                        entities.model_dump_json(indent=2),
                        encoding="utf-8",
                    )
                self._emit(PhaseCompleted(phase="entities", message="...done"))

            _bible_path, _version_bible_path, bible_entities, bible_warnings = write_entities_bible(
                campaign_root=self.campaigns_root / self.campaign,
                version_dir=version_dir,
                entities_path=entities_path,
            )
            if bible_warnings:
                for warning in bible_warnings:
                    self._emit(
                        PhaseWarning(
                            phase="entities",
                            message="Entities continuity warning",
                            warning=warning,
                        )
                    )

            # Episode-scoped cast: only names from 02_entities.json, records from the bible.
            # Downstream text stages (beater/script/prompt) use this, not the full campaign bible.
            episode_entities = write_episode_entities(
                entities_path=entities_path,
                bible=bible_entities,
                output_path=episode_entities_path,
            )

        story_bible: StoryBibleCheckpoint | None = None
        if self._should_run_stage("beater"):
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
                try:
                    # Prepare and save prompts before model call.
                    # The exact rendered strings are also the ones sent to the model.
                    beater_system_prompt, beater_user_prompt = prepare_beater_prompts(
                        version_dir=version_dir,
                        content=raw.content,
                        world=episode_entities,
                        scene_count=scene_count,
                        raw_quotes=[
                            {"text": quote.text, "attribution": quote.attribution}
                            for quote in raw.quotes
                        ],
                        system_prompt_path=prompt_template_paths[MASTER_BEATER_SYSTEM_PROMPT_FILENAME],
                        user_prompt_path=prompt_template_paths[MASTER_BEATER_USER_PROMPT_FILENAME],
                    )
                    story_bible = create_story_bible(
                        raw_checkpoint_path=raw_path,
                        entities_checkpoint_path=episode_entities_path,
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
                    self._emit_prompt_template_mismatch_warning("beater", exc)

        script_pages: list[ScriptCheckpoint] | None = None
        script_generated_this_run = False
        if self._should_run_stage("script"):
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
                try:
                    if self.generation_mode == "panel":
                        script_pages = _generate_script_pages_panel_mode(
                            version_dir=version_dir,
                            story_bible_path=story_bible_path,
                            raw_path=raw_path,
                            episode_entities_path=episode_entities_path,
                            episode_entities=cast(WorldStateInput, episode_entities),
                            total_pages=self.total_pages,
                            script_model=self.script_model,
                            prompt_template_paths=prompt_template_paths,
                        )
                    else:
                        story_bible_pages = write_story_bible_pages(
                            story_bible_checkpoint_path=story_bible_path,
                            output_paths=story_bible_page_paths,
                            total_pages=self.total_pages,
                        )
                        generated_pages: list[ScriptCheckpoint] = []
                        for page_number, (story_bible_page, story_bible_page_path, script_page_path) in enumerate(
                            zip(story_bible_pages, story_bible_page_paths, script_page_paths),
                            start=1,
                        ):
                            script_system_prompt, script_user_prompt = prepare_scriptwriter_prompts(
                                version_dir=version_dir,
                                world=cast(WorldStateInput, episode_entities),
                                story_bible=story_bible_page,
                                system_prompt_path=prompt_template_paths[SCRIPTWRITER_SYSTEM_PROMPT_FILENAME],
                                user_prompt_path=prompt_template_paths[SCRIPTWRITER_USER_PROMPT_FILENAME],
                                page_number=page_number,
                                output_suffix=f"page_{page_number:03d}",
                            )
                            generated_pages.append(
                                write_script(
                                    raw_checkpoint_path=raw_path,
                                    entities_checkpoint_path=episode_entities_path,
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
                    self._emit_prompt_template_mismatch_warning("script", exc)

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
        if self._should_run_stage("style"):
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
                    self._emit_prompt_template_mismatch_warning("style", exc)

        page_prompt: str | None = None
        page_prompts: list[tuple[Path, str]] = []
        expected_prompt_paths: list[Path] = []
        prompt_script_pages = script_pages if self.skip_style else styled_script_pages
        if self._should_run_stage("prompt"):
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
                if self.generation_mode == "panel":
                    for page_number, prompt_script in enumerate(prompt_script_pages, start=1):
                        for panel in prompt_script.panels:
                            expected_prompt_paths.append(_panel_prompt_path(version_dir, page_number, panel.index))
                else:
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
                            if self.generation_mode == "panel":
                                for panel in prompt_script.panels:
                                    try:
                                        panel_script = ScriptCheckpoint(
                                            url=prompt_script.url,
                                            title=prompt_script.title,
                                            author=prompt_script.author,
                                            model=prompt_script.model,
                                            panel_count=1,
                                            total_pages=1,
                                            pages=[
                                                Page(
                                                    page_number=panel.page_number,
                                                    panel_count=1,
                                                    panels=[panel],
                                                )
                                            ],
                                            generation_errors=prompt_script.generation_errors,
                                            scripted_at=prompt_script.scripted_at,
                                        )
                                        prompt_text = prepare_page_prompt_template(
                                            version_dir=version_dir,
                                            world=episode_entities,
                                            script=panel_script,
                                            art_template=art_template,
                                            template_path=prompt_template_paths[PAGE_PROMPT_TEMPLATE_FILENAME],
                                            aspect_ratio=self.aspect_ratio,
                                            output_suffix=f"page_{page_number:03d}_panel_{panel.index:03d}",
                                            generation_mode="panel",
                                        )
                                    except Exception as exc:
                                        self._emit(
                                            PhaseWarning(
                                                phase="prompt",
                                                message=f"Failed to save interpolated panel {page_number}.{panel.index} prompt template",
                                                warning=str(exc),
                                            )
                                        )
                                        continue

                                    page_output_path = _panel_prompt_path(version_dir, page_number, panel.index)
                                    page_output_path.write_text(prompt_text, encoding="utf-8")
                                    page_prompts.append((page_output_path, prompt_text))
                                continue

                            try:
                                prompt_text = prepare_page_prompt_template(
                                    version_dir=version_dir,
                                    world=episode_entities,
                                    script=prompt_script,
                                    art_template=art_template,
                                    template_path=prompt_template_paths[PAGE_PROMPT_TEMPLATE_FILENAME],
                                    aspect_ratio=self.aspect_ratio,
                                    generation_mode="page",
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
                        self._emit_prompt_template_mismatch_warning("prompt", exc)

        image_generation_paths: list[str] = []
        image_generation_errors: list[str] = []
        # Image generation is beyond the ordered text stages; only when not stopping early.
        if self.generate_images and self.stop_after is None:
            self._emit(
                PhaseStarted(
                    phase="image_generation",
                    message="Generating images...",
                    details={"model": self.image_generation_model, "page_count": len(list(version_dir.glob("04_page_*_prompt.txt")))},
                )
            )
            try:
                image_generation_paths, image_generation_errors = self._run_image_generation_stage(version_dir)
                if image_generation_paths:
                    self._emit(
                        PhaseCompleted(
                            phase="image_generation",
                            message="...done",
                            details={"page_count": len(image_generation_paths)},
                        )
                    )
                    stitched_paths, stitching_errors = self._run_stitching_stage(version_dir)
                    if stitched_paths:
                        self._emit(
                            PhaseCompleted(
                                phase="stitching",
                                message="...done",
                                details={"page_count": len(stitched_paths)},
                            )
                        )
                    image_generation_errors.extend(stitching_errors)
                else:
                    self._emit(
                        PhaseSkipped(
                            phase="image_generation",
                            message="Skipped",
                            reason="no prompt files available",
                        )
                    )
            except Exception as exc:
                image_generation_errors.append(f"image_generation: {exc}")
                self._emit(
                    PhaseError(
                        phase="image_generation",
                        message="Image generation failed",
                        error=str(exc),
                        exception=exc,
                    )
                )

            errors.extend(image_generation_errors)

        # Determine final status
        checkpoints_created = []
        if entities_path.exists():
            checkpoints_created.append("entities")
        if story_bible_path.exists():
            checkpoints_created.append("story_bible")
        if all(path.exists() for path in script_page_paths):
            checkpoints_created.append("script")
        if all(path.exists() for path in styled_script_page_paths) and not self.skip_style:
            checkpoints_created.append("styled_script")
        if expected_prompt_paths and all(path.exists() for path in expected_prompt_paths):
            checkpoints_created.append("page_prompt")

        failed_phases = []
        if self._should_run_stage("beater") and story_bible is None:
            failed_phases.append("beater")
        if self._should_run_stage("script") and script_pages is None and story_bible is not None:
            failed_phases.append("script")
        if (
            self._should_run_stage("style")
            and styled_script_pages is None
            and script_pages is not None
            and not self.skip_style
        ):
            failed_phases.append("style")
        if self._should_run_stage("prompt") and page_prompt is None and prompt_script_pages:
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

        # Working is the mutable next-run source of truth: mirror this version's
        # artifacts so edits and recomputed checkpoints land in working.
        if self._working_dir is None:
            self._working_dir = _ensure_working_dir(version_dir.parent)
        _sync_version_to_working(version_dir, self._working_dir)

        return {
            "raw_text": raw.model_dump(),
            "entities": entities.model_dump() if entities is not None else None,
            "story_bible": story_bible.model_dump() if story_bible is not None else None,
            "script": [checkpoint.model_dump() for checkpoint in script_pages] if script_pages is not None else None,
            "styled_script": [checkpoint.model_dump() for checkpoint in styled_script_pages] if styled_script_pages is not None else None,
            "page_prompt": {
                "output_path": str(prompts_path),
                "prompt": page_prompt,
            } if page_prompt is not None else None,
            "images": image_generation_paths,
            "errors": errors,
            "error_details": error_details,
            "version": version_name,
            "version_dir": str(version_dir),
            "working_dir": str(self._working_dir),
            "run_config": self.run_config_dict(),
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
            "Overrides --art-style when both are set."
        ),
    )
    parser.add_argument(
        "--art-style",
        default=None,
        help=(
            "Named art style id or stem (e.g. 'bundled:brutalist', 'brutalist'). "
            "Resolves against bundled prompts/art_direction/ then campaign art_direction/."
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

    art_style_id = args.art_style
    if art_style_id and ":" not in art_style_id:
        # Bare stem: prefer campaign match, else bundled.
        matches = [
            s
            for s in list_art_styles(Path(args.campaigns_root), args.campaign)
            if s.stem == art_style_id
        ]
        campaign_match = next((s for s in matches if s.source == "campaign"), None)
        bundled_match = next((s for s in matches if s.source == "bundled"), None)
        chosen = campaign_match or bundled_match
        if chosen is None:
            raise SystemExit(f"Unknown art style stem: {art_style_id!r}")
        art_style_id = chosen.id

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
        art_style=art_style_id,
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
            if pipeline._working_dir is not None:
                pipeline._working_dir.mkdir(parents=True, exist_ok=True)
                (pipeline._working_dir / "run_status.json").write_text(
                    status_json, encoding="utf-8"
                )
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
    working_dir = Path(cast(str, result.get("working_dir") or ""))
    if not working_dir:
        working_dir = Path(cast(str, result["version_dir"])).parent / WORKING_DIR_NAME
    working_dir.mkdir(parents=True, exist_ok=True)
    (working_dir / "run_status.json").write_text(status_json, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(_run_cli())
