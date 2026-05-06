from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from entities import (
    WorldStateCheckpoint,
    build_entities_from_raw,
)
from prompter import (
    ART_DIRECTION_TEMPLATE_FILENAME,
    DEFAULT_ART_DIRECTION_TEMPLATE_PATH,
    _load_art_template,
    generate_page_prompt,
)
from prompt_saver import (
    prepare_architect_prompts,
    prepare_page_prompt_template,
    prepare_scriptwriter_prompts,
    prepare_style_integrator_prompts,
)
from prompt_templates import (
    DEFAULT_PROMPTS_DIR,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    PROMPT_TEMPLATE_FILENAMES,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
    STORY_ARCHITECT_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)
from scriptwriter import WorldStateInput
from style_integrator import StyleIntegrationPartialFailure, integrate_style
from scraper import RawTextCheckpoint, normalize_recap_version, scrape_scrybequill
from scriptwriter import ScriptCheckpoint, write_script
from story_architect import StoryArchitectureCheckpoint, architect_story

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAMPAIGNS_ROOT = Path("campaigns")
INDEX_FILENAME = "index.json"
EPISODE_META_FILENAME = "episode_meta.json"

RerunFrom = Literal["scrape", "entities", "architect", "script", "style", "prompt"]
RerunFromArg = Literal["scrape", "entities", "architect", "script", "style", "prompt", "analyze"]

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
    episode_dir: Path, rerun_from: RerunFromArg | None
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

    # Backward compatibility for older callers that still pass "analyze".
    if rerun_from == "analyze":
        rerun_from = "entities"

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
                "02_5_story_architecture.json",
                "03_script.json",
                "03_5_styled_script.json",
                "04_page_prompt.txt",
            ],
            "scrape": [],
            "entities": ["01_raw_text.json"],
            "architect": ["01_raw_text.json", "02_entities.json"],
            "script": ["01_raw_text.json", "02_entities.json", "02_5_story_architecture.json"],
            "style": [
                "01_raw_text.json",
                "02_entities.json",
                "02_5_story_architecture.json",
                "03_script.json",
            ],
            "prompt": [
                "01_raw_text.json",
                "02_entities.json",
                "02_5_story_architecture.json",
                "03_script.json",
                "03_5_styled_script.json",
            ],
        }
        files_to_copy = _CHECKPOINTS_TO_COPY.get(rerun_from, [])
        
        for fname in files_to_copy:
            src = prev_dir / fname
            if src.exists():
                shutil.copy2(src, version_dir / fname)

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
        analysis_model: str = "qwen2.5:7b",
        architect_model: str = "qwen2.5:7b",
        script_model: str = "qwen2.5:7b",
        style_model: str = "qwen2.5:7b",
        panel_count: int = 6,
        art_style_template: Path | None = None,
        story_architect_system_prompt: Path | None = None,
        story_architect_user_prompt: Path | None = None,
        scriptwriter_system_prompt: Path | None = None,
        scriptwriter_user_prompt: Path | None = None,
        style_integrator_system_prompt: Path | None = None,
        style_integrator_user_prompt: Path | None = None,
        page_prompt_template: Path | None = None,
        rerun_from: RerunFromArg | None = None,
        recap_version: str = "standard",
        skip_style: bool = False,
    ):
        if rerun_from == "analyze":
            rerun_from = "entities"
        self.url = url
        self.campaign = campaign
        self.campaigns_root = campaigns_root
        self.analysis_model = analysis_model
        self.architect_model = architect_model
        self.script_model = script_model
        self.style_model = style_model
        self.panel_count = panel_count
        self.art_style_template = art_style_template
        self.story_architect_system_prompt = story_architect_system_prompt
        self.story_architect_user_prompt = story_architect_user_prompt
        self.scriptwriter_system_prompt = scriptwriter_system_prompt
        self.scriptwriter_user_prompt = scriptwriter_user_prompt
        self.style_integrator_system_prompt = style_integrator_system_prompt
        self.style_integrator_user_prompt = style_integrator_user_prompt
        self.page_prompt_template = page_prompt_template
        self.rerun_from: RerunFromArg | None = rerun_from
        self.recap_version = normalize_recap_version(recap_version)
        self.skip_style = skip_style

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
            STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME: self.story_architect_system_prompt
            or self._campaign_prompt_path(STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME),
            STORY_ARCHITECT_USER_PROMPT_FILENAME: self.story_architect_user_prompt
            or self._campaign_prompt_path(STORY_ARCHITECT_USER_PROMPT_FILENAME),
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
        captured_paths: dict[str, Path] = {}
        for filename, source_path in prompt_paths.items():
            if not source_path.exists():
                raise FileNotFoundError(
                    f"Prompt template file not found at {source_path}."
                )

            version_prompt_path = version_dir / filename
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
            print("[1/5] Scraping...")
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_raw_path = Path(tmpdir) / "01_raw_text.json"
                raw = await scrape_scrybequill(
                    url=self.url,
                    checkpoint_path=tmp_raw_path,
                    recap_version=self.recap_version,
                )
            print(f"      ...done  (title: {raw.title!r}, recap: {self.recap_version})")

            episode_dir = _resolve_episode_dir(
                self.campaigns_root, self.campaign, self.url, raw.title
            )
            version_dir, version_name = _create_version_dir(episode_dir, self.rerun_from)
            self._version_dir = version_dir
            print(f"      episode: {episode_dir.name}/{version_name}")

            raw_path = version_dir / "01_raw_text.json"
            raw_path.write_text(
                json.dumps(raw.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
            )

            # Bootstrap campaign-level art template on first run.
            self._ensure_campaign_art_template()
        else:
            episode_dir = _episode_dir(self.campaigns_root, self.campaign, existing_episode)
            version_dir, version_name = _create_version_dir(episode_dir, self.rerun_from)
            self._version_dir = version_dir
            print(f"      episode: {episode_dir.name}/{version_name}")
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
                    print(f"[1/5] Recap variant changed to {self.recap_version!r} - invalidating downstream checkpoints")
                    entities_path = version_dir / "02_entities.json"
                    story_architecture_path = version_dir / "02_5_story_architecture.json"
                    script_path = version_dir / "03_script.json"
                    styled_script_path = version_dir / "03_5_styled_script.json"
                    prompts_path = version_dir / "04_page_prompt.txt"
                    entities_path.unlink(missing_ok=True)
                    story_architecture_path.unlink(missing_ok=True)
                    script_path.unlink(missing_ok=True)
                    styled_script_path.unlink(missing_ok=True)
                    prompts_path.unlink(missing_ok=True)
                else:
                    print(f"[1/5] Scraping...skipped (checkpoint exists, recap: {self.recap_version})")
            else:
                print("[1/5] Scraping...")
                raw = await scrape_scrybequill(
                    url=self.url,
                    checkpoint_path=raw_path,
                    recap_version=self.recap_version,
                )
                print(f"      ...done  (title: {raw.title!r}, recap: {self.recap_version})")

        self._ensure_campaign_prompt_templates()
        entities_path = version_dir / "02_entities.json"
        story_architecture_path = version_dir / "02_5_story_architecture.json"
        script_path = version_dir / "03_script.json"
        styled_script_path = version_dir / "03_5_styled_script.json"
        prompts_path = version_dir / "04_page_prompt.txt"
        template_path = self._resolve_art_template(version_dir, episode_dir)
        prompt_template_paths = self._capture_prompt_templates_for_version(
            self._resolve_prompt_templates(),
            version_dir,
        )
        errors: list[str] = []

        if entities_path.exists():
            print("[2/5] Building entities...skipped (checkpoint exists)")
            entities = WorldStateCheckpoint.model_validate_json(
                entities_path.read_text(encoding="utf-8")
            )
        else:
            print("[2/5] Building entities from scraped notes...")
            entities = build_entities_from_raw(
                raw_checkpoint_path=raw_path,
                output_path=entities_path,
                model_label="scraper-direct",
            )
            print("      ...done")

        story_architecture: StoryArchitectureCheckpoint | None = None
        if story_architecture_path.exists():
            print("[3/5] Building story architecture...skipped (checkpoint exists)")
            story_architecture = StoryArchitectureCheckpoint.model_validate_json(
                story_architecture_path.read_text(encoding="utf-8")
            )
        else:
            print(
                f"[3/5] Building story architecture...  (model: {self.architect_model}, panels: {self.panel_count})"
            )
            try:
                # Prepare and save prompts before model call
                prepare_architect_prompts(
                    version_dir=version_dir,
                    world=entities,
                    panel_count=self.panel_count,
                    raw_quotes=[
                        {"text": quote.text, "attribution": quote.attribution}
                        for quote in raw.quotes
                    ],
                    system_prompt_path=prompt_template_paths[STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME],
                    user_prompt_path=prompt_template_paths[STORY_ARCHITECT_USER_PROMPT_FILENAME],
                )
            except Exception as exc:
                print(f"      ...WARNING (failed to save interpolated prompts): {exc}")
            try:
                story_architecture = architect_story(
                    raw_checkpoint_path=raw_path,
                    entities_checkpoint_path=entities_path,
                    output_path=story_architecture_path,
                    model=self.architect_model,
                    panel_count=self.panel_count,
                    system_prompt_path=prompt_template_paths[STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME],
                    user_prompt_path=prompt_template_paths[STORY_ARCHITECT_USER_PROMPT_FILENAME],
                )
                print("      ...done")
            except Exception as exc:
                errors.append(f"story_architecture: {exc}")
                print(
                    "      ...ERROR (story architecture generation failed — skipping phases 4 & 5): "
                    f"{exc}"
                )

        script: ScriptCheckpoint | None = None
        script_generated_this_run = False
        if story_architecture is None:
            print("[4/5] Writing script...skipped (no story architecture)")
        elif script_path.exists():
            print("[4/5] Writing script...skipped (checkpoint exists)")
            script = ScriptCheckpoint.model_validate_json(script_path.read_text(encoding="utf-8"))
        else:
            print(f"[4/5] Writing script...  (model: {self.script_model})")
            try:
                # Prepare and save prompts before model call
                prepare_scriptwriter_prompts(
                    version_dir=version_dir,
                    world=cast(WorldStateInput, entities),
                    architecture=story_architecture,
                    system_prompt_path=prompt_template_paths[SCRIPTWRITER_SYSTEM_PROMPT_FILENAME],
                    user_prompt_path=prompt_template_paths[SCRIPTWRITER_USER_PROMPT_FILENAME],
                )
            except Exception as exc:
                print(f"      ...WARNING (failed to save interpolated prompts): {exc}")
            try:
                script = write_script(
                    raw_checkpoint_path=raw_path,
                    entities_checkpoint_path=entities_path,
                    story_architecture_checkpoint_path=story_architecture_path,
                    output_path=script_path,
                    model=self.script_model,
                    system_prompt_path=prompt_template_paths[SCRIPTWRITER_SYSTEM_PROMPT_FILENAME],
                    user_prompt_path=prompt_template_paths[SCRIPTWRITER_USER_PROMPT_FILENAME],
                )
                script_generated_this_run = True
                print("      ...done")
            except Exception as exc:
                errors.append(f"script: {exc}")
                print(f"      ...ERROR (script generation failed — skipping phases 3 & 4): {exc}")

        if script_generated_this_run and script is not None and script.generation_errors:
            for generation_error in script.generation_errors:
                errors.append(f"script: {generation_error}")
                print(f"      ...ERROR (script validation): {generation_error}")

        styled_script: ScriptCheckpoint | None = None
        if script is None:
            print("[4.5/5] Integrating art style...skipped (no script)")
        elif self.skip_style:
            print("[4.5/5] Integrating art style...skipped (--skip-style)")
            styled_script = script
        elif styled_script_path.exists():
            print("[4.5/5] Integrating art style...skipped (checkpoint exists)")
            styled_script = ScriptCheckpoint.model_validate_json(
                styled_script_path.read_text(encoding="utf-8")
            )
        else:
            template_path = self._capture_art_template_for_version(template_path, version_dir)
            print(f"[4.5/5] Integrating art style...  (model: {self.style_model}, template: {template_path})")
            try:
                # Prepare and save prompts before model call
                art_template = _load_art_template(template_path)
                prepare_style_integrator_prompts(
                    version_dir=version_dir,
                    script=script,
                    art_template=art_template,
                    system_prompt_path=prompt_template_paths[STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME],
                    user_prompt_path=prompt_template_paths[STYLE_INTEGRATOR_USER_PROMPT_FILENAME],
                )
            except Exception as exc:
                print(f"      ...WARNING (failed to save interpolated prompts): {exc}")
            try:
                styled_script = integrate_style(
                    script_checkpoint_path=script_path,
                    art_style_template_path=template_path,
                    output_path=styled_script_path,
                    model=self.style_model,
                    system_prompt_path=prompt_template_paths[STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME],
                    user_prompt_path=prompt_template_paths[STYLE_INTEGRATOR_USER_PROMPT_FILENAME],
                )
                print("      ...done")
            except StyleIntegrationPartialFailure as exc:
                styled_script = exc.checkpoint
                errors.append(f"style: {exc}")
                print(f"      ...WARN (style integration partially failed): {exc}")
            except Exception as exc:
                errors.append(f"style: {exc}")
                print(f"      ...ERROR (style integration failed — skipping phase 4): {exc}")

        page_prompt: str | None = None
        if styled_script is None:
            print("[5/5] Generating page prompt...skipped (no styled script)")
        elif prompts_path.exists():
            print("[5/5] Generating page prompt...skipped (checkpoint exists)")
            page_prompt = prompts_path.read_text(encoding="utf-8")
        else:
            template_path = self._capture_art_template_for_version(template_path, version_dir)
            prompt_script_path = script_path if self.skip_style else styled_script_path
            print(f"[5/5] Generating page prompt...  (template: {template_path})")
            if prompt_script_path.exists():
                try:
                    prompt_script = ScriptCheckpoint.model_validate_json(
                        prompt_script_path.read_text(encoding="utf-8")
                    )
                    art_template = _load_art_template(template_path)
                    prepare_page_prompt_template(
                        version_dir=version_dir,
                        world=entities,
                        script=prompt_script,
                        art_template=art_template,
                        template_path=prompt_template_paths[PAGE_PROMPT_TEMPLATE_FILENAME],
                    )
                except Exception as exc:
                    print(f"      ...WARNING (failed to save interpolated prompts): {exc}")
            try:
                page_prompt = generate_page_prompt(
                    script_checkpoint_path=prompt_script_path,
                    entities_checkpoint_path=entities_path,
                    art_style_template_path=template_path,
                    output_path=prompts_path,
                    page_prompt_template_path=prompt_template_paths[PAGE_PROMPT_TEMPLATE_FILENAME],
                )
                print("      ...done")
            except Exception as exc:
                errors.append(f"page_prompt: {exc}")
                print(f"      ...ERROR (page prompt generation failed): {exc}")

        return {
            "raw_text": raw.model_dump(),
            "entities": entities.model_dump(),
            "story_architecture": story_architecture.model_dump() if story_architecture is not None else None,
            "script": script.model_dump() if script is not None else None,
            "styled_script": styled_script.model_dump() if styled_script is not None else None,
            "page_prompt": {
                "output_path": str(prompts_path),
                "prompt": page_prompt,
            } if page_prompt is not None else None,
            "errors": errors,
            "version": version_name,
            "version_dir": str(version_dir),
        }


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
        "--architect-model",
        default="qwen2.5:7b",
        help="Ollama model name used for Phase 3 story architecture",
    )
    parser.add_argument(
        "--script-model",
        default="qwen2.5:7b",
        help="Ollama model name used for Phase 4 scripting",
    )
    parser.add_argument(
        "--style-model",
        default="qwen2.5:7b",
        help="Ollama model name used for Phase 4.5 art style integration",
    )
    parser.add_argument(
        "--panel-count",
        default=6,
        type=int,
        help="Number of comic panels to generate in Phase 3",
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
        "--story-architect-system-prompt",
        default=None,
        help=(
            "Explicit path to the story architect system prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME} "
            "and bootstraps it from prompts/ on first use."
        ),
    )
    parser.add_argument(
        "--story-architect-user-prompt",
        default=None,
        help=(
            "Explicit path to the story architect user prompt template. "
            f"If omitted, the pipeline uses campaigns/<campaign>/{STORY_ARCHITECT_USER_PROMPT_FILENAME} "
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
        choices=["scrape", "entities", "architect", "script", "style", "prompt", "analyze"],
        default=None,
        help=(
            "Invalidate checkpoints from this phase onward and rerun. "
            "Prior phases are cloned from the last version. "
            "Options: scrape, entities, architect, script, style, prompt (analyze accepted as legacy alias)"
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
    if rerun_from_arg == "analyze":
        rerun_from_arg = "entities"

    pipeline = ComicPipeline(
        url=args.url,
        campaign=args.campaign,
        campaigns_root=Path(args.campaigns_root),
        architect_model=args.architect_model,
        script_model=args.script_model,
        style_model=args.style_model,
        panel_count=args.panel_count,
        art_style_template=Path(args.art_style_template) if args.art_style_template else None,
        story_architect_system_prompt=Path(args.story_architect_system_prompt)
        if args.story_architect_system_prompt
        else None,
        story_architect_user_prompt=Path(args.story_architect_user_prompt)
        if args.story_architect_user_prompt
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
    )
    try:
        result = await pipeline.run()
    except Exception as exc:
        status_blob = {
            "status": "failed",
            "campaign": args.campaign,
            "errors": [str(exc)],
        }
        status_json = json.dumps(status_blob, indent=2)
        print(status_json)
        if pipeline._version_dir is not None:
            (pipeline._version_dir / "run_status.json").write_text(status_json, encoding="utf-8")
        raise

    checkpoint_keys = (
        "entities",
        "story_architecture",
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
    }
    status_json = json.dumps(status_blob, indent=2)
    print(status_json)
    run_status_path = Path(cast(str, result["version_dir"])) / "run_status.json"
    run_status_path.write_text(status_json, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(_run_cli())
