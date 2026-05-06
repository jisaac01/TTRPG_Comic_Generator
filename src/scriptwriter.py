from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, cast

from pydantic import BaseModel, Field

from entities import Character, Location, StoryBeat
from prompt_templates import (
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    render_prompt_template,
)
from scraper import RawTextCheckpoint
from story_architect import StoryArchitectureCheckpoint


class WorldStateInput(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    characters: list[Character]
    locations: list[Location]
    beats: list[StoryBeat]
    analyzed_at: str


class Panel(BaseModel):
    index: int = Field(ge=1)
    panel_scale: Literal["small", "medium", "large", "splash"]
    panel_shape: Literal["standard", "wide", "tall", "inset", "irregular"]
    setting: str = Field(min_length=1)
    visual_action: str = Field(min_length=1)
    dialogue_overlay: list[str] = Field(default_factory=list)
    held_items_before: dict[str, list[str]] = Field(default_factory=dict)
    held_items_after: dict[str, list[str]] = Field(default_factory=dict)


class ScriptPayload(BaseModel):
    panels: list[Panel] = Field(min_length=1)


class ScriptCheckpoint(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    panel_count: int = Field(ge=1)
    panels: list[Panel] = Field(min_length=1)
    generation_errors: list[str] = Field(default_factory=list)
    scripted_at: str


ScriptGenerator = Callable[[WorldStateInput, StoryArchitectureCheckpoint, str], ScriptPayload]


def _build_instructor_client():
    import instructor
    from openai import OpenAI

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")
    openai_client = OpenAI(base_url=base_url, api_key=api_key)
    return instructor.from_openai(openai_client, mode=instructor.Mode.JSON)


def _format_entities_for_prompt(
    world: WorldStateInput,
    raw_quotes: list[tuple[str, str | None]] | None = None,
) -> str:
    characters_blob = "\n".join(
        f"- {char.name}: {char.description}" for char in world.characters
    )
    locations_blob = "\n".join(
        f"- {location.name}: {location.appearance}" for location in world.locations
    )
    beats_parts: list[str] = []
    for beat in world.beats:
        beat_str = f"- Beat {beat.index}: {beat.beat}"
        if beat.highlights:
            highlights_str = ", ".join(beat.highlights)
            beat_str += f" ({highlights_str})"
        beats_parts.append(beat_str)
    beats_blob = "\n".join(beats_parts)

    quote_lines: list[str] = []

    # Prefer quotes from the scraped raw checkpoint because entities no longer
    # embed quote text under beats.
    for quote_text, attribution in raw_quotes or []:
        cleaned_text = " ".join(quote_text.split()).strip()
        if not cleaned_text:
            continue
        speaker = " ".join((attribution or "").split()).strip() or "Unknown"
        quote_lines.append(f"- {speaker}: \"{cleaned_text}\"")

    quotes_blob = "\n".join(quote_lines)

    return (
        f"Characters:\n{characters_blob or '- none'}\n\n"
        f"Locations:\n{locations_blob or '- none'}\n\n"
        f"Story beats:\n{beats_blob or '- none'}\n\n"
        f"Reference quotes:\n{quotes_blob or '- none'}"
    )


def _format_story_architecture_for_prompt(architecture: StoryArchitectureCheckpoint) -> str:
    panel_payload: list[dict[str, object]] = []
    for panel in architecture.panels:
        panel_payload.append(
            {
                "index": panel.index,
                "beat_indices": panel.beat_indices,
                "beat_summary": panel.beat_summary,
                "story_purpose": panel.story_purpose,
                "panel_scale": panel.panel_scale,
                "panel_shape": panel.panel_shape,
                "setting_brief": panel.setting_brief,
                "character_focus": panel.character_focus,
                "must_include": panel.must_include,
                "dialogue_goals": panel.dialogue_goals,
                "continuity_notes": panel.continuity_notes,
            }
        )
    return json.dumps(panel_payload, indent=2, ensure_ascii=False)


def _generate_with_instructor_ollama(
    world: WorldStateInput,
    architecture: StoryArchitectureCheckpoint,
    raw_quotes: list[tuple[str, str | None]],
    model: str,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> ScriptPayload:
    client = _build_instructor_client()
    title = world.title or "Untitled story"
    entities_context = _format_entities_for_prompt(world, raw_quotes)

    system_prompt = render_prompt_template(
        SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
    )

    user_prompt = render_prompt_template(
        SCRIPTWRITER_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        title=title,
        panel_count=len(architecture.panels),
        entities_context=entities_context,
        story_architecture=_format_story_architecture_for_prompt(architecture),
    )

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
    architecture: StoryArchitectureCheckpoint,
) -> list[Panel]:
    normalized: list[Panel] = []
    for idx, panel in enumerate(panels, start=1):
        architecture_panel = architecture.panels[idx - 1] if idx <= len(architecture.panels) else None
        normalized.append(
            Panel(
                index=idx,
                panel_scale=(
                    architecture_panel.panel_scale if architecture_panel is not None else panel.panel_scale
                ),
                panel_shape=(
                    architecture_panel.panel_shape if architecture_panel is not None else panel.panel_shape
                ),
                setting=panel.setting,
                visual_action=panel.visual_action,
                dialogue_overlay=panel.dialogue_overlay,
                held_items_before=panel.held_items_before,
                held_items_after=panel.held_items_after,
            )
        )
    return normalized


def _validate_item_continuity(panels: list[Panel]) -> None:
    for current, nxt in zip(panels, panels[1:]):
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
    story_architecture_checkpoint_path: Path = Path(
        "campaigns/<campaign>/<episode>/v001/02_5_story_architecture.json"
    ),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_script.json"),
    model: str = "qwen2.5:7b",
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    generator: ScriptGenerator | None = None,
) -> ScriptCheckpoint:
    raw = RawTextCheckpoint.model_validate_json(raw_checkpoint_path.read_text(encoding="utf-8"))
    world = WorldStateInput.model_validate_json(entities_checkpoint_path.read_text(encoding="utf-8"))
    architecture = StoryArchitectureCheckpoint.model_validate_json(
        story_architecture_checkpoint_path.read_text(encoding="utf-8")
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
                architecture,
                [(quote.text, quote.attribution) for quote in raw.quotes],
                model,
                system_prompt_path=system_prompt_path,
                user_prompt_path=user_prompt_path,
            )
        else:
            payload = generate_fn(world, architecture, model)
    except Exception as exc:
        raise RuntimeError(f"Generation failed before validation: {exc}") from exc

    panels = _normalize_panels(payload.panels, architecture)
    expected_panel_count = len(architecture.panels)
    if len(panels) != expected_panel_count:
        generation_errors.append(
            "Architecture alignment failed: expected "
            f"{expected_panel_count} panels from story architecture, received {len(panels)}. "
            "Accepting panel-count mismatch."
        )
    try:
        _validate_item_continuity(panels)
    except ValueError as exc:
        generation_errors.append(f"Continuity validation failed: {exc}. Accepting validation failure.")

    checkpoint = ScriptCheckpoint(
        url=raw.url,
        title=raw.title,
        author=raw.author,
        model=model,
        panel_count=len(panels),
        panels=panels,
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
        "--story-architecture-input",
        required=True,
        help=(
            "Input story architecture checkpoint path "
            "(e.g. campaigns/<campaign>/<episode>/v001/02_5_story_architecture.json)"
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output script checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/03_script.json)",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Ollama model name",
    )
    args = parser.parse_args()
    checkpoint = write_script(
        raw_checkpoint_path=Path(args.raw_input),
        entities_checkpoint_path=Path(args.entities_input),
        story_architecture_checkpoint_path=Path(args.story_architecture_input),
        output_path=Path(args.output),
        model=args.model,
    )
    print(f"Saved {len(checkpoint.panels)} panels to {args.output}")


if __name__ == "__main__":
    _run_cli()
