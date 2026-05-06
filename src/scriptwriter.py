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


ScriptGenerator = Callable[[str, WorldStateInput, str, int], ScriptPayload]


class PanelPlan(BaseModel):
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)
    beat_count: int = Field(ge=0)


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


def _resolve_panel_plan(requested_panel_count: int, beats: list[StoryBeat]) -> PanelPlan:
    beat_count = len(beats)
    minimum = max(1, requested_panel_count - 1)
    maximum = max(minimum, requested_panel_count + 1)
    return PanelPlan(minimum=minimum, maximum=maximum, beat_count=beat_count)


def _generate_with_instructor_ollama(
    content: str,
    world: WorldStateInput,
    raw_quotes: list[tuple[str, str | None]],
    model: str,
    panel_plan: PanelPlan,
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
        minimum_panel_target=panel_plan.minimum,
        maximum_panel_target=panel_plan.maximum,
        entities_context=entities_context,
        story_text=content,
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


def _normalize_panels(panels: list[Panel]) -> list[Panel]:
    normalized: list[Panel] = []
    for idx, panel in enumerate(panels, start=1):
        normalized.append(
            Panel(
                index=idx,
                panel_scale=panel.panel_scale,
                panel_shape=panel.panel_shape,
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
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/03_script.json"),
    model: str = "qwen2.5:7b",
    panel_count: int = 6,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    generator: ScriptGenerator | None = None,
) -> ScriptCheckpoint:
    if panel_count < 1:
        raise ValueError("panel_count must be >= 1")

    raw = RawTextCheckpoint.model_validate_json(raw_checkpoint_path.read_text(encoding="utf-8"))
    world = WorldStateInput.model_validate_json(entities_checkpoint_path.read_text(encoding="utf-8"))
    panel_plan = _resolve_panel_plan(panel_count, world.beats)
    generation_errors: list[str] = []

    if generator is None:
        generate_fn: ScriptGenerator | None = None
    else:
        generate_fn = generator

    try:
        if generate_fn is None:
            payload = _generate_with_instructor_ollama(
                raw.content,
                world,
                [(quote.text, quote.attribution) for quote in raw.quotes],
                model,
                panel_plan,
                system_prompt_path=system_prompt_path,
                user_prompt_path=user_prompt_path,
            )
        else:
            payload = generate_fn(raw.content, world, model, panel_count)
    except Exception as exc:
        raise RuntimeError(f"Generation failed before validation: {exc}") from exc

    panels = _normalize_panels(payload.panels)
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
        "--output",
        required=True,
        help="Output script checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/03_script.json)",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Ollama model name",
    )
    parser.add_argument(
        "--panel-count",
        default=6,
        type=int,
        help="Target number of comic panels (soft target; final count may vary by ±1)",
    )
    args = parser.parse_args()
    checkpoint = write_script(
        raw_checkpoint_path=Path(args.raw_input),
        entities_checkpoint_path=Path(args.entities_input),
        output_path=Path(args.output),
        model=args.model,
        panel_count=args.panel_count,
    )
    print(f"Saved {len(checkpoint.panels)} panels to {args.output}")


if __name__ == "__main__":
    _run_cli()
