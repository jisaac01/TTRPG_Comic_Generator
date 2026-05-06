from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from entities import StoryBeat, WorldStateCheckpoint
from prompt_templates import (
    STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
    STORY_ARCHITECT_USER_PROMPT_FILENAME,
    render_prompt_template,
)
from scraper import RawTextCheckpoint


class NotableQuote(BaseModel):
    text: str = Field(min_length=1)
    speaker: str = Field(min_length=1)
    attribution_context: str = Field(min_length=1)


class ArchitecturePanel(BaseModel):
    index: int = Field(ge=1)
    beat_indices: list[int] = Field(min_length=1)
    beat_summary: str = Field(min_length=1)
    story_purpose: str = Field(min_length=1)
    panel_scale: Literal["small", "medium", "large", "splash"]
    panel_shape: Literal["standard", "wide", "tall", "inset", "irregular"]
    setting_brief: str = Field(min_length=1)
    character_focus: list[str] = Field(default_factory=list)
    notable_set_dressing: list[str] = Field(min_length=1)
    notable_quotes: list[NotableQuote] = Field(default_factory=list)
    dialogue_goals: list[str] = Field(default_factory=list)
    continuity_notes: list[str] = Field(default_factory=list)


class StoryArchitecturePayload(BaseModel):
    panels: list[ArchitecturePanel] = Field(min_length=1)


class StoryArchitectureCheckpoint(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    target_panel_count: int = Field(ge=1)
    panels: list[ArchitecturePanel] = Field(min_length=1)
    generation_errors: list[str] = Field(default_factory=list)
    architected_at: str


ArchitectureGenerator = Callable[[str, WorldStateCheckpoint, str, int], StoryArchitecturePayload]


def _build_instructor_client():
    import instructor
    from openai import OpenAI

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")
    openai_client = OpenAI(base_url=base_url, api_key=api_key)
    return instructor.from_openai(openai_client, mode=instructor.Mode.JSON)


def _format_beats_for_prompt(beats: list[StoryBeat]) -> str:
    beat_lines: list[str] = []
    for beat in beats:
        highlights = ", ".join(beat.highlights)
        beat_lines.append(
            f"- Beat {beat.index}: {beat.beat}"
            + (f" ({highlights})" if highlights else "")
        )
    return "\n".join(beat_lines) or "- none"


def _format_quotes_for_prompt(quotes: list[dict[str, str | None]] | None = None) -> str:
    quote_lines: list[str] = []
    for quote in quotes or []:
        text = quote.get("text", "").strip() if isinstance(quote, dict) else ""
        attribution = quote.get("attribution", "").strip() if isinstance(quote, dict) else ""
        if text:
            speaker = attribution or "Unknown"
            quote_lines.append(f"- {speaker}: \"{text}\"")
    return "\n".join(quote_lines) or "- none"


def _normalize_quote_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _format_entities_for_prompt(world: WorldStateCheckpoint) -> str:
    character_lines = "\n".join(
        f"- {character.name}: {character.description}" for character in world.characters
    )
    location_lines = "\n".join(
        f"- {location.name}: {location.appearance}" for location in world.locations
    )
    beats_blob = _format_beats_for_prompt(world.beats)
    return (
        f"Characters:\n{character_lines or '- none'}\n\n"
        f"Locations:\n{location_lines or '- none'}\n\n"
        f"Story beats:\n{beats_blob}"
    )


def _generate_with_instructor_ollama(
    content: str,
    world: WorldStateCheckpoint,
    model: str,
    panel_count: int,
    quotes: list[dict[str, str | None]] | None = None,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> StoryArchitecturePayload:
    client = _build_instructor_client()

    system_prompt = render_prompt_template(
        STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
    )
    user_prompt = render_prompt_template(
        STORY_ARCHITECT_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        title=world.title or "Untitled story",
        panel_count=panel_count,
        entities_context=_format_entities_for_prompt(world),
        story_text=content,
        reference_quotes=_format_quotes_for_prompt(quotes),
    )

    return client.chat.completions.create(
        model=model,
        temperature=0.4,
        response_model=StoryArchitecturePayload,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )


def _normalize_panels(
    panels: list[ArchitecturePanel],
    source_quote_lookup: dict[str, str],
) -> tuple[list[ArchitecturePanel], list[str]]:
    normalized: list[ArchitecturePanel] = []
    quote_errors: list[str] = []
    for idx, panel in enumerate(panels, start=1):
        normalized_notable_quotes: list[NotableQuote] = []
        for notable_quote in panel.notable_quotes:
            normalized_text = _normalize_quote_text(notable_quote.text)
            canonical_attribution = source_quote_lookup.get(normalized_text)
            if canonical_attribution is None:
                quote_errors.append(
                    "Notable quote validation failed in panel "
                    f"{idx}: quote text not found in reference quotes: {notable_quote.text!r}."
                )
                continue
            normalized_notable_quotes.append(
                NotableQuote(
                    text=normalized_text,
                    speaker=notable_quote.speaker,
                    attribution_context=canonical_attribution,
                )
            )

        normalized.append(
            ArchitecturePanel(
                index=idx,
                beat_indices=panel.beat_indices,
                beat_summary=panel.beat_summary,
                story_purpose=panel.story_purpose,
                panel_scale=panel.panel_scale,
                panel_shape=panel.panel_shape,
                setting_brief=panel.setting_brief,
                character_focus=panel.character_focus,
                notable_set_dressing=panel.notable_set_dressing,
                notable_quotes=normalized_notable_quotes,
                dialogue_goals=panel.dialogue_goals,
                continuity_notes=panel.continuity_notes,
            )
        )
    return normalized, quote_errors


def _find_uncovered_beat_indices(
    beats: list[StoryBeat],
    panels: list[ArchitecturePanel],
) -> list[int]:
    referenced = {beat_index for panel in panels for beat_index in panel.beat_indices}
    return [beat.index for beat in beats if beat.index not in referenced]


def architect_story(
    raw_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/01_raw_text.json"),
    entities_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/02_entities.json"),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/02_5_story_architecture.json"),
    model: str = "qwen2.5:7b",
    panel_count: int = 6,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    generator: ArchitectureGenerator | None = None,
) -> StoryArchitectureCheckpoint:
    if panel_count < 1:
        raise ValueError("panel_count must be >= 1")

    raw = RawTextCheckpoint.model_validate_json(raw_checkpoint_path.read_text(encoding="utf-8"))
    world = WorldStateCheckpoint.model_validate_json(
        entities_checkpoint_path.read_text(encoding="utf-8")
    )

    quotes_list: list[dict[str, str | None]] = []
    source_quote_lookup: dict[str, str] = {}
    for quote in raw.quotes:
        normalized_text = _normalize_quote_text(quote.text)
        if not normalized_text:
            continue
        attribution = quote.attribution or "Unknown attribution"
        quotes_list.append({"text": normalized_text, "attribution": attribution})
        source_quote_lookup[normalized_text] = attribution

    if generator is not None:
        payload = generator(raw.content, world, model, panel_count)
    else:
        payload = _generate_with_instructor_ollama(
            raw.content,
            world,
            model,
            panel_count,
            quotes=quotes_list,
            system_prompt_path=system_prompt_path,
            user_prompt_path=user_prompt_path,
        )

    panels, quote_errors = _normalize_panels(payload.panels, source_quote_lookup)
    generation_errors: list[str] = []
    generation_errors.extend(quote_errors)
    uncovered_beat_indices = _find_uncovered_beat_indices(world.beats, panels)
    if uncovered_beat_indices:
        generation_errors.append(
            "Beat coverage validation failed: missing beat indices "
            f"{uncovered_beat_indices}. Accepting validation failure."
        )

    checkpoint = StoryArchitectureCheckpoint(
        url=raw.url,
        title=raw.title,
        author=raw.author,
        model=model,
        target_panel_count=panel_count,
        panels=panels,
        generation_errors=generation_errors,
        architected_at=datetime.now(timezone.utc).isoformat(),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return checkpoint


def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a story architecture checkpoint that allocates beats to comic panels."
    )
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
        help="Output architect checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/02_5_story_architecture.json)",
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
        help="Target number of architected panels",
    )

    args = parser.parse_args()
    checkpoint = architect_story(
        raw_checkpoint_path=Path(args.raw_input),
        entities_checkpoint_path=Path(args.entities_input),
        output_path=Path(args.output),
        model=args.model,
        panel_count=args.panel_count,
    )
    print(f"Saved {len(checkpoint.panels)} architected panels to {args.output}")


if __name__ == "__main__":
    _run_cli()