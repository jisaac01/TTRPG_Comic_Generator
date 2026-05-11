from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from entities import Character, Location, StoryBeat
from llm_client import build_instructor_client
from model_defaults import DEFAULT_MODEL
from prompt_templates import (
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    render_prompt_template,
)
from scraper import RawTextCheckpoint
from master_beater import StoryBibleCheckpoint


class WorldStateInput(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    player_characters: list[Character]
    npcs: list[Character]
    locations: list[Location]
    beats: list[StoryBeat]
    analyzed_at: str


class Panel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    page_number: int = Field(ge=1, description="Which page this panel appears on")
    panel_scale: Literal["small", "medium", "large", "splash"]
    panel_shape: Literal["standard", "wide", "tall", "inset", "irregular"]
    setting: str = Field(min_length=1)
    visual_action: str = Field(min_length=1)
    dialogue_overlay: list[str] = Field(default_factory=list, description="format: 'Character: text'")
    held_items_before: dict[str, list[str]] = Field(default_factory=dict)
    held_items_after: dict[str, list[str]] = Field(default_factory=dict)
    narrative_overlays_and_text_direction: list[str] = Field(
        default_factory=list,
        description=(
            "Optional sparse list for captions/voiceover/chyron/sfx/text direction. "
            "Use prefixed entries such as 'CAPTION: ...', 'V.O.: ...', "
            "'CHYRON: ...', 'SFX: ...', or 'TEXT-DIRECTION: ...'."
        ),
    )


class Page(BaseModel):
    page_number: int = Field(ge=1)
    panel_count: int = Field(ge=1)
    panels: list[Panel] = Field(min_length=1)


class ScriptPayload(BaseModel):
    pages: list[Page] = Field(min_length=1)


class ScriptCheckpoint(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    panel_count: int = Field(ge=1)
    total_pages: int = Field(ge=1, default=1)
    pages: list[Page] = Field(min_length=1)
    generation_errors: list[str] = Field(default_factory=list)
    scripted_at: str
    
    @property
    def panels(self) -> list[Panel]:
        """Flatten all panels from all pages for compatibility."""
        flat = []
        for page in self.pages:
            flat.extend(page.panels)
        return flat


ScriptGenerator = Callable[[WorldStateInput, StoryBibleCheckpoint, str], ScriptPayload]

SCENE_HEADER_RE = re.compile(r"(?m)^Scene\s+(\d+):")


def _build_instructor_client(model: str):
    return build_instructor_client(model)


def _format_entities_for_prompt(world: WorldStateInput) -> str:
    pc_blob = "\n".join(
        f"- {char.name}: {char.description}" for char in world.player_characters
    )
    npc_blob = "\n".join(
        f"- {char.name}: {char.description}" for char in world.npcs
    )
    locations_blob = "\n".join(
        f"- {location.name}: {location.appearance}" for location in world.locations
    )

    return (
        f"Player Characters:\n{pc_blob or '- none'}\n\n"
        f"NPCs:\n{npc_blob or '- none'}\n\n"
        f"Locations:\n{locations_blob or '- none'}"
    )


def _format_story_bible_for_prompt(story_bible: StoryBibleCheckpoint) -> str:
    """Format the story bible text for inclusion in the scriptwriter prompt."""
    return story_bible.story_bible


def split_story_bible_into_scenes(story_bible: StoryBibleCheckpoint) -> list[str]:
    """Split a story bible into ordered scene blocks using Scene N: headers."""
    matches = list(SCENE_HEADER_RE.finditer(story_bible.story_bible))
    if not matches:
        raise ValueError("Story bible must contain at least one 'Scene N:' header.")

    scenes: list[str] = []
    expected_number = 1
    for idx, match in enumerate(matches):
        scene_number = int(match.group(1))
        if scene_number != expected_number:
            raise ValueError(
                "Story bible scenes must be sequential starting at 1: "
                f"expected Scene {expected_number}, found Scene {scene_number}."
            )

        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(story_bible.story_bible)
        scene_text = story_bible.story_bible[match.start():end].strip()
        header_line, _, body = scene_text.partition("\n")
        if not body.strip():
            raise ValueError(f"Story bible scene body is empty for {header_line.strip()}.")

        scenes.append(scene_text)
        expected_number += 1

    return scenes


def bucket_scenes_into_pages(scenes: list[str], total_pages: int) -> list[list[str]]:
    """Distribute ordered scenes into contiguous page buckets as evenly as possible."""
    if total_pages < 1:
        raise ValueError("total_pages must be at least 1.")
    if len(scenes) < total_pages:
        raise ValueError(
            "Story bible must contain at least as many scenes as requested pages: "
            f"received {len(scenes)} scenes for {total_pages} pages."
        )

    base = len(scenes) // total_pages
    remainder = len(scenes) % total_pages
    buckets: list[list[str]] = []
    cursor = 0
    for page_index in range(total_pages):
        size = base + (1 if page_index < remainder else 0)
        page_scenes = scenes[cursor:cursor + size]
        if not page_scenes:
            raise ValueError(f"Page {page_index + 1} received no scenes during bucketing.")
        buckets.append(page_scenes)
        cursor += size

    return buckets


def build_story_bible_page_checkpoints(
    story_bible: StoryBibleCheckpoint,
    total_pages: int,
) -> list[StoryBibleCheckpoint]:
    """Create page-scoped story bible checkpoints from a monolithic story bible."""
    scenes = split_story_bible_into_scenes(story_bible)
    page_buckets = bucket_scenes_into_pages(scenes, total_pages)
    checkpoints: list[StoryBibleCheckpoint] = []
    for page_scenes in page_buckets:
        checkpoints.append(
            StoryBibleCheckpoint(
                url=story_bible.url,
                title=story_bible.title,
                author=story_bible.author,
                model=story_bible.model,
                scene_count=len(page_scenes),
                story_bible="\n\n".join(page_scenes),
                generation_errors=list(story_bible.generation_errors),
                created_at=story_bible.created_at,
            )
        )
    return checkpoints


def write_story_bible_pages(
    story_bible_checkpoint_path: Path,
    output_paths: list[Path],
    total_pages: int,
) -> list[StoryBibleCheckpoint]:
    """Split a story bible checkpoint into page-scoped checkpoint files."""
    if len(output_paths) != total_pages:
        raise ValueError(
            f"Expected {total_pages} story bible output paths, received {len(output_paths)}."
        )

    story_bible = StoryBibleCheckpoint.model_validate_json(
        story_bible_checkpoint_path.read_text(encoding="utf-8")
    )
    checkpoints = build_story_bible_page_checkpoints(story_bible, total_pages)
    for output_path, checkpoint in zip(output_paths, checkpoints):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return checkpoints


def renumber_script_page_checkpoints(
    checkpoints: list[ScriptCheckpoint],
) -> list[ScriptCheckpoint]:
    """Apply global panel indices and explicit page numbers across page checkpoints."""
    renumbered: list[ScriptCheckpoint] = []
    next_index = 1
    for page_number, checkpoint in enumerate(checkpoints, start=1):
        if len(checkpoint.pages) != 1:
            raise ValueError(
                f"Chunked script checkpoint for page {page_number} must contain exactly one page."
            )

        source_page = checkpoint.pages[0]
        page_panels: list[Panel] = []
        for panel in source_page.panels:
            page_panels.append(
                Panel(
                    index=next_index,
                    page_number=page_number,
                    panel_scale=panel.panel_scale,
                    panel_shape=panel.panel_shape,
                    setting=panel.setting,
                    visual_action=panel.visual_action,
                    dialogue_overlay=panel.dialogue_overlay,
                    held_items_before=panel.held_items_before,
                    held_items_after=panel.held_items_after,
                    narrative_overlays_and_text_direction=panel.narrative_overlays_and_text_direction,
                )
            )
            next_index += 1

        page = Page(
            page_number=page_number,
            panel_count=len(page_panels),
            panels=page_panels,
        )
        renumbered.append(
            ScriptCheckpoint(
                url=checkpoint.url,
                title=checkpoint.title,
                author=checkpoint.author,
                model=checkpoint.model,
                panel_count=checkpoint.panel_count,
                total_pages=1,
                pages=[page],
                generation_errors=list(checkpoint.generation_errors),
                scripted_at=checkpoint.scripted_at,
            )
        )

    return renumbered


def apply_cross_page_continuity_errors(
    checkpoints: list[ScriptCheckpoint],
) -> list[ScriptCheckpoint]:
    """Validate continuity across ordered page checkpoints and record soft failures."""
    if not checkpoints:
        return []

    all_pages: list[Page] = []
    for checkpoint in checkpoints:
        all_pages.extend(checkpoint.pages)

    continuity_error: str | None = None
    try:
        _validate_item_continuity(all_pages)
    except ValueError as exc:
        continuity_error = (
            f"Continuity validation failed: {exc}. Accepting validation failure."
        )

    if continuity_error is None:
        return checkpoints

    updated: list[ScriptCheckpoint] = []
    for checkpoint in checkpoints:
        generation_errors = list(checkpoint.generation_errors)
        generation_errors.append(continuity_error)
        updated.append(
            ScriptCheckpoint(
                url=checkpoint.url,
                title=checkpoint.title,
                author=checkpoint.author,
                model=checkpoint.model,
                panel_count=checkpoint.panel_count,
                total_pages=checkpoint.total_pages,
                pages=checkpoint.pages,
                generation_errors=generation_errors,
                scripted_at=checkpoint.scripted_at,
            )
        )

    return updated


def _generate_with_instructor_ollama(
    world: WorldStateInput,
    story_bible: StoryBibleCheckpoint,
    model: str,
    system_prompt_text: str,
    user_prompt_text: str,
) -> ScriptPayload:
    client = _build_instructor_client(model)
    system_prompt = system_prompt_text
    user_prompt = user_prompt_text
    return client.chat.completions.create(
        model=model,
        temperature=0.7,
        response_model=ScriptPayload,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )


def _normalize_panels(
    panels: list[Panel],
    story_bible: StoryBibleCheckpoint,
) -> list[Panel]:
    normalized: list[Panel] = []
    for idx, panel in enumerate(panels, start=1):
        # With story_bible (text-only), we don't have per-panel metadata like panel_scale/shape.
        # The LLM generates these values based on the narrative context.
        # We just normalize the panel indices to ensure consistency.
        normalized.append(
            Panel(
                index=idx,
                page_number=panel.page_number,
                panel_scale=panel.panel_scale,
                panel_shape=panel.panel_shape,
                setting=panel.setting,
                visual_action=panel.visual_action,
                dialogue_overlay=panel.dialogue_overlay,
                held_items_before=panel.held_items_before,
                held_items_after=panel.held_items_after,
                narrative_overlays_and_text_direction=panel.narrative_overlays_and_text_direction,
            )
        )
    return normalized


def _group_panels_into_pages(
    panels: list[Panel],
    total_pages: int,
) -> list[Page]:
    """Group panels into pages based on page_number field."""
    
    if not panels:
        raise ValueError("Cannot create pages from empty panels list")
    
    # Group panels by page_number
    pages_dict: dict[int, list[Panel]] = {}
    for panel in panels:
        page_num = panel.page_number
        if page_num not in pages_dict:
            pages_dict[page_num] = []
        pages_dict[page_num].append(panel)
    
    # Build Page objects, sorted by page_number
    pages: list[Page] = []
    for page_num in sorted(pages_dict.keys()):
        page_panels = pages_dict[page_num]
        pages.append(Page(
            page_number=page_num,
            panel_count=len(page_panels),
            panels=page_panels,
        ))
    
    # Verify we have the expected number of pages
    if len(pages) != total_pages:
        raise ValueError(
            f"Expected {total_pages} pages but panels have {len(pages)} unique page numbers"
        )
    
    return pages


def _validate_item_continuity(pages: list[Page]) -> None:
    # Flatten all panels across all pages for continuity validation
    all_panels = []
    for page in pages:
        all_panels.extend(page.panels)
    
    for current, nxt in zip(all_panels, all_panels[1:]):
        for character, after_items in current.held_items_after.items():
            next_before = nxt.held_items_before.get(character)
            if next_before is None:
                if not after_items:
                    continue
                raise ValueError(
                    f"Continuity break between panel {current.index} and {nxt.index}: "
                    f"missing held_items_before for {character}."
                )

            # Characters can gain new items between panels, but they should not
            # silently lose items they were still holding in the prior panel.
            missing_items = sorted(set(after_items) - set(next_before))
            if missing_items:
                raise ValueError(
                    f"Continuity break between panel {current.index} and {nxt.index} for {character}: "
                    f"missing_in_next_before={missing_items}, after={after_items}, next_before={next_before}."
                )


def write_script(
    raw_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/01_raw_text.json"),
    entities_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/02_entities.json"),
    story_bible_checkpoint_path: Path = Path(
        "campaigns/<campaign>/<episode>/v001/02_5_story_bible.json"
    ),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_script.json"),
    *,
    system_prompt_text: str,
    user_prompt_text: str,
    model: str = DEFAULT_MODEL,
    total_pages: int = 1,
    generator: ScriptGenerator | None = None,
) -> ScriptCheckpoint:
    
    raw = RawTextCheckpoint.model_validate_json(raw_checkpoint_path.read_text(encoding="utf-8"))
    world = WorldStateInput.model_validate_json(entities_checkpoint_path.read_text(encoding="utf-8"))
    story_bible = StoryBibleCheckpoint.model_validate_json(
        story_bible_checkpoint_path.read_text(encoding="utf-8")
    )
    generation_errors: list[str] = []

    if generator is None:
        generate_fn: ScriptGenerator | None = None
    else:
        generate_fn = generator

    try:
        if generate_fn is None:
            payload = _generate_with_instructor_ollama(
                world,
                story_bible,
                model,
                system_prompt_text=system_prompt_text,
                user_prompt_text=user_prompt_text,
            )
        else:
            payload = generate_fn(world, story_bible, model)
    except Exception as exc:
        raise RuntimeError(f"Generation failed before validation: {exc}") from exc

    # Flatten panels from payload pages for normalization
    flat_panels: list[Panel] = []
    for page in payload.pages:
        flat_panels.extend(page.panels)
    
    # Normalize panels
    panels = _normalize_panels(flat_panels, story_bible)
    
    # Distribute panels sequentially across pages
    # If total_pages=3 and we have 18 panels, page 1 gets 0-5, page 2 gets 6-11, page 3 gets 12-17
    panels_per_page = len(panels) // total_pages
    remainder = len(panels) % total_pages
    
    panels_with_pages: list[Panel] = []
    for idx, panel in enumerate(panels):
        # Distribute panels sequentially: page 1 gets first panels_per_page panels, etc.
        if remainder > 0 and idx < remainder * (panels_per_page + 1):
            # First 'remainder' pages get an extra panel
            page_num = (idx // (panels_per_page + 1)) + 1
        else:
            # Remaining pages get base count
            offset = remainder * (panels_per_page + 1) if remainder > 0 else 0
            adjusted_idx = idx - offset
            page_num = (adjusted_idx // panels_per_page) + 1 + remainder
        
        panels_with_pages.append(
            Panel(
                index=panel.index,
                page_number=page_num,
                panel_scale=panel.panel_scale,
                panel_shape=panel.panel_shape,
                setting=panel.setting,
                visual_action=panel.visual_action,
                dialogue_overlay=panel.dialogue_overlay,
                held_items_before=panel.held_items_before,
                held_items_after=panel.held_items_after,
                narrative_overlays_and_text_direction=panel.narrative_overlays_and_text_direction,
            )
        )
    
    expected_panel_count = story_bible.scene_count
    if len(panels_with_pages) != expected_panel_count:
        generation_errors.append(
            "Scene count alignment failed: expected "
            f"{expected_panel_count} panels from story bible, received {len(panels_with_pages)}. "
            "Accepting panel-count mismatch."
        )
    
    # Group panels into pages
    try:
        pages = _group_panels_into_pages(panels_with_pages, total_pages)
    except ValueError as exc:
        raise RuntimeError(f"Failed to group panels into pages: {exc}") from exc
    
    # Validate item continuity across all panels
    try:
        _validate_item_continuity(pages)
    except ValueError as exc:
        generation_errors.append(f"Continuity validation failed: {exc}. Accepting validation failure.")

    checkpoint = ScriptCheckpoint(
        url=raw.url,
        title=raw.title,
        author=raw.author,
        model=model,
        panel_count=len(panels_with_pages),
        total_pages=total_pages,
        pages=pages,
        generation_errors=generation_errors,
        scripted_at=datetime.now(timezone.utc).isoformat(),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return checkpoint


def _run_cli() -> None:
    parser = argparse.ArgumentParser(description="Generate a continuity-aware comic script checkpoint.")
    parser.add_argument(
        "--raw-input",
        required=True,
        help="Input raw text checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/01_raw_text.json)",
    )
    parser.add_argument(
        "--entities-input",
        required=True,
        help="Input entities checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/02_entities.json)",
    )
    parser.add_argument(
        "--story-bible-input",
        required=True,
        help=(
            "Input story bible checkpoint path "
            "(e.g. campaigns/<campaign>/<episode>/v001/02_5_story_bible.json)"
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output script checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/03_script.json)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Ollama model name",
    )
    args = parser.parse_args()

    world = WorldStateInput.model_validate_json(Path(args.entities_input).read_text(encoding="utf-8"))
    story_bible = StoryBibleCheckpoint.model_validate_json(
        Path(args.story_bible_input).read_text(encoding="utf-8")
    )
    title = world.title or "Untitled story"
    entities_context = _format_entities_for_prompt(world)
    system_prompt_text = render_prompt_template(
        SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    )
    user_prompt_text = render_prompt_template(
        SCRIPTWRITER_USER_PROMPT_FILENAME,
        title=title,
        panel_count=story_bible.scene_count,
        entities_context=entities_context,
        story_bible=_format_story_bible_for_prompt(story_bible),
    )

    checkpoint = write_script(
        raw_checkpoint_path=Path(args.raw_input),
        entities_checkpoint_path=Path(args.entities_input),
        story_bible_checkpoint_path=Path(args.story_bible_input),
        output_path=Path(args.output),
        model=args.model,
        system_prompt_text=system_prompt_text,
        user_prompt_text=user_prompt_text,
    )
    print(f"Saved {len(checkpoint.panels)} panels to {args.output}")


if __name__ == "__main__":
    _run_cli()
