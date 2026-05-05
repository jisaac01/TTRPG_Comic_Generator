from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from entities import (
    WorldStateCheckpoint,
    build_entities_from_raw,
)
from prompter import (
    ART_DIRECTION_TEMPLATE_FILENAME,
    DEFAULT_ART_DIRECTION_TEMPLATE,
    generate_page_prompt,
)
from scraper import RawTextCheckpoint, normalize_recap_version, scrape_scrybequill
from scriptwriter import ScriptCheckpoint, write_script

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAMPAIGNS_ROOT = Path("campaigns")
INDEX_FILENAME = "index.json"
EPISODE_META_FILENAME = "episode_meta.json"

RerunFrom = Literal["scrape", "entities", "script", "prompt"]

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

    If a previous version exists, clone its checkpoints as the baseline so
    that stages prior to *rerun_from* are preserved without re-running.

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
        # Copy all checkpoint files from previous version as baseline.
        for src in prev_dir.iterdir():
            if src.is_file():
                shutil.copy2(src, version_dir / src.name)

        # Delete forward from the requested rerun point.
        _PHASE_FILES: dict[RerunFrom, list[str]] = {
            "scrape": ["01_raw_text.json", "02_entities.json", "03_script.json", "04_page_prompt.txt"],
            "entities": ["02_entities.json", "03_script.json", "04_page_prompt.txt"],
            "script": ["03_script.json", "04_page_prompt.txt"],
            "prompt": ["04_page_prompt.txt"],
        }
        if rerun_from:
            for fname in _PHASE_FILES[rerun_from]:
                (version_dir / fname).unlink(missing_ok=True)

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
        script_model: str = "qwen2.5:7b",
        panel_count: int = 6,
        art_style_template: Path | None = None,
        rerun_from: RerunFrom | None = None,
        recap_version: str = "standard",
    ):
        if rerun_from == "analyze":
            rerun_from = "entities"
        self.url = url
        self.campaign = campaign
        self.campaigns_root = campaigns_root
        self.analysis_model = analysis_model
        self.script_model = script_model
        self.panel_count = panel_count
        self.art_style_template = art_style_template
        self.rerun_from = rerun_from
        self.recap_version = normalize_recap_version(recap_version)

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
        campaign_template.write_text(
            f"{json.dumps(DEFAULT_ART_DIRECTION_TEMPLATE, indent=2)}\n",
            encoding="utf-8",
        )

    async def run(self) -> dict[str, object]:
        # Phase 1: scrape first so we have the title for episode resolution.
        # We need a temporary path to store the raw checkpoint before the episode
        # directory is resolved (title comes from the scrape).
        #
        # Strategy: scrape into a temp directory first if no episode exists yet,
        # then resolve the episode dir, then move the file into the version dir.

        existing_episode = _lookup_episode(self.campaigns_root, self.campaign, self.url)

        if existing_episode is None:
            # First run: scrape to get the title so we can slug the episode folder.
            print("[1/4] Scraping...")
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
                    print(f"[1/4] Recap variant changed to {self.recap_version!r} - invalidating downstream checkpoints")
                    entities_path = version_dir / "02_entities.json"
                    script_path = version_dir / "03_script.json"
                    prompts_path = version_dir / "04_page_prompt.txt"
                    entities_path.unlink(missing_ok=True)
                    script_path.unlink(missing_ok=True)
                    prompts_path.unlink(missing_ok=True)
                else:
                    print(f"[1/4] Scraping...skipped (checkpoint exists, recap: {self.recap_version})")
            else:
                print("[1/4] Scraping...")
                raw = await scrape_scrybequill(
                    url=self.url,
                    checkpoint_path=raw_path,
                    recap_version=self.recap_version,
                )
                print(f"      ...done  (title: {raw.title!r}, recap: {self.recap_version})")

        entities_path = version_dir / "02_entities.json"
        script_path = version_dir / "03_script.json"
        prompts_path = version_dir / "04_page_prompt.txt"
        template_path = self._resolve_art_template(version_dir, episode_dir)
        errors: list[str] = []

        if entities_path.exists():
            print("[2/4] Building entities...skipped (checkpoint exists)")
            entities = WorldStateCheckpoint.model_validate_json(
                entities_path.read_text(encoding="utf-8")
            )
        else:
            print("[2/4] Building entities from scraped notes...")
            entities = build_entities_from_raw(
                raw_checkpoint_path=raw_path,
                output_path=entities_path,
                model_label="scraper-direct",
            )
            print("      ...done")

        script: ScriptCheckpoint | None = None
        script_generated_this_run = False
        if script_path.exists():
            print("[3/4] Writing script...skipped (checkpoint exists)")
            script = ScriptCheckpoint.model_validate_json(script_path.read_text(encoding="utf-8"))
        else:
            print(f"[3/4] Writing script...  (model: {self.script_model}, panels: {self.panel_count})")
            try:
                script = write_script(
                    raw_checkpoint_path=raw_path,
                    entities_checkpoint_path=entities_path,
                    output_path=script_path,
                    model=self.script_model,
                    panel_count=self.panel_count,
                )
                script_generated_this_run = True
                print("      ...done")
            except (ValueError, RuntimeError) as exc:
                errors.append(f"script: {exc}")
                print(f"      ...ERROR (script generation failed — skipping phases 3 & 4): {exc}")

        if script_generated_this_run and script is not None and script.generation_errors:
            for generation_error in script.generation_errors:
                errors.append(f"script: {generation_error}")
                print(f"      ...ERROR (script validation): {generation_error}")

        page_prompt: str | None = None
        if script is None:
            print("[4/4] Generating page prompt...skipped (no script)")
        elif prompts_path.exists():
            print("[4/4] Generating page prompt...skipped (checkpoint exists)")
            page_prompt = prompts_path.read_text(encoding="utf-8")
        else:
            print(f"[4/4] Generating page prompt...  (template: {template_path})")
            try:
                page_prompt = generate_page_prompt(
                    script_checkpoint_path=script_path,
                    entities_checkpoint_path=entities_path,
                    art_style_template_path=template_path,
                    output_path=prompts_path,
                )
                print("      ...done")
            except Exception as exc:
                errors.append(f"page_prompt: {exc}")
                print(f"      ...ERROR (page prompt generation failed): {exc}")

        return {
            "raw_text": raw.model_dump(),
            "entities": entities.model_dump(),
            "script": script.model_dump() if script is not None else None,
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
            "  # Re-run with new art style (reuse all previous checkpoints except prompt):\n"
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
        "--script-model",
        default="qwen2.5:7b",
        help="Ollama model name used for Phase 3 scripting",
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
        "--rerun-from",
        choices=["scrape", "entities", "script", "prompt", "analyze"],
        default=None,
        help=(
            "Invalidate checkpoints from this phase onward and rerun. "
            "Prior phases are cloned from the last version. "
            "Options: scrape, entities, script, prompt (analyze accepted as legacy alias)"
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

    args = parser.parse_args()
    rerun_from_arg = args.rerun_from
    if rerun_from_arg == "analyze":
        rerun_from_arg = "entities"

    pipeline = ComicPipeline(
        url=args.url,
        campaign=args.campaign,
        campaigns_root=Path(args.campaigns_root),
        script_model=args.script_model,
        panel_count=args.panel_count,
        art_style_template=Path(args.art_style_template) if args.art_style_template else None,
        rerun_from=rerun_from_arg,
        recap_version=args.recap_version,
    )
    result = await pipeline.run()
    checkpoint_keys = ("raw_text", "entities", "script", "page_prompt")
    failed = [key for key in checkpoint_keys if result.get(key) is None]
    print(
        json.dumps(
            {
                "status": "partial" if failed else "ok",
                "campaign": args.campaign,
                "version": result["version"],
                "version_dir": result["version_dir"],
                "checkpoints": [key for key in checkpoint_keys if result.get(key) is not None],
                "failed": failed,
                "errors": result.get("errors", []),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(_run_cli())
