from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from entities import StoryBeat, WorldStateCheckpoint, format_character_details
from llm_client import build_openai_client
from model_defaults import DEFAULT_MODEL
from prompt_templates import (
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    render_prompt_template,
)
from scraper import RawTextCheckpoint

SCENE_HEADER_RE = re.compile(r"(?m)^Scene\s+(\d+):")


class StoryBibleCheckpoint(BaseModel):
    """In-memory story bible. On disk this is plain text (`02_5_story_bible.txt`)."""

    url: str = ""
    title: str | None = None
    author: str | None = None
    model: str = ""
    scene_count: int = Field(ge=1)
    story_bible: str = Field(min_length=1, description="Text-only narrative scene breakdown")
    generation_errors: list[str] = Field(default_factory=list)
    created_at: str = ""


StoryBibleGenerator = Callable[[str, WorldStateCheckpoint, str, int], str]


def count_story_bible_scenes(text: str) -> int:
    """Return the number of Scene N: headers in story bible text.

    Does not require numbering to start at 1 — page/panel slices keep original
    scene numbers from the full bible. Use ``validate_full_story_bible_scenes``
    when writing a complete bible.
    """
    matches = list(SCENE_HEADER_RE.finditer(text))
    if not matches:
        raise ValueError("Story bible must contain at least one 'Scene N:' header.")
    return len(matches)


def validate_full_story_bible_scenes(text: str) -> int:
    """Require Scene 1..N sequential headers with non-empty bodies. Return count."""
    matches = list(SCENE_HEADER_RE.finditer(text))
    if not matches:
        raise ValueError("Story bible must contain at least one 'Scene N:' header.")

    expected_number = 1
    for idx, match in enumerate(matches):
        scene_number = int(match.group(1))
        if scene_number != expected_number:
            raise ValueError(
                "Story bible scenes must be sequential starting at 1: "
                f"expected Scene {expected_number}, found Scene {scene_number}."
            )
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        scene_text = text[match.start():end].strip()
        header_line, _, body = scene_text.partition("\n")
        if not body.strip():
            raise ValueError(f"Story bible scene body is empty for {header_line.strip()}.")
        expected_number += 1
    return len(matches)


def write_story_bible(path: Path, story_bible_text: str) -> None:
    """Write story bible prose to a plain-text checkpoint file."""
    text = story_bible_text.strip()
    if not text:
        raise ValueError("Story bible text is empty")
    count_story_bible_scenes(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def load_story_bible(path: Path) -> StoryBibleCheckpoint:
    """Load a plain-text story bible and derive scene_count from Scene N: headers."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Story bible is empty: {path}")
    scene_count = count_story_bible_scenes(text)
    return StoryBibleCheckpoint(
        scene_count=scene_count,
        story_bible=text,
    )


def _format_entities_for_prompt(world: WorldStateCheckpoint) -> str:
    pc_lines = "\n".join(
        format_character_details(character, bullet=True)
        for character in world.player_characters
    )
    npc_lines = "\n".join(
        format_character_details(character, bullet=True) for character in world.npcs
    )
    location_lines = "\n".join(
        f"- {location.name}: {location.appearance}" for location in world.locations
    )
    beats_blob = _format_beats_for_prompt(world.beats)
    return (
        f"Player Characters:\n{pc_lines or '- none'}\n\n"
        f"NPCs:\n{npc_lines or '- none'}\n\n"
        f"Locations:\n{location_lines or '- none'}\n\n"
        f"Story beats:\n{beats_blob}"
    )


def _format_beats_for_prompt(beats: list[StoryBeat]) -> str:
    beat_lines: list[str] = []
    for beat in beats:
        highlights = ", ".join(beat.highlights)
        beat_lines.append(
            f"- Beat {beat.index}: {beat.beat}"
            + (f" ({highlights})" if highlights else "")
        )
    return "\n".join(beat_lines) or "- none"


def _preserve_case(source: str, target: str) -> str:
    if source.isupper():
        return target.upper()
    if source.istitle():
        return target.title()
    return target


def _normalize_aliases_in_text(text: str, world: WorldStateCheckpoint) -> str:
    """Replace whole-word alias mentions with canonical character names.

    Replacements apply anywhere in the text (not only sentence starts). Longer
    aliases are applied first so multi-word variants win over shorter ones.
    """
    replacements: list[tuple[str, str]] = []
    for character in [*world.player_characters, *world.npcs]:
        canonical_name = character.name.strip()
        if not canonical_name:
            continue
        for alias in character.aliases or []:
            alias_text = alias.strip()
            if not alias_text or alias_text.casefold() == canonical_name.casefold():
                continue
            replacements.append((alias_text, canonical_name))

    # Longer aliases first (e.g. "Maisie Faye" before a hypothetical "Faye").
    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)

    normalized = text
    for alias_text, canonical_name in replacements:
        pattern = re.compile(
            rf"(?<!\w)({re.escape(alias_text)})(?!\w)",
            re.IGNORECASE,
        )
        normalized = pattern.sub(
            lambda match, canon=canonical_name: _preserve_case(match.group(1), canon),
            normalized,
        )

    return normalized


def _format_quotes_for_prompt(quotes: list[dict[str, str | None]] | None = None) -> str:
    quote_lines: list[str] = []
    for quote in quotes or []:
        if isinstance(quote, dict):
            text = (quote.get("text") or "").strip()
            attribution = (quote.get("attribution") or "Unknown").strip()
        else:
            text = ""
            attribution = "Unknown"
        if text:
            speaker = attribution or "Unknown"
            quote_lines.append(f"- {speaker}: \"{text}\"")
    return "\n".join(quote_lines) or "- none"


def _build_instructor_client(model: str):
    return build_openai_client(model)


def _generate_with_ollama(
    content: str,
    world: WorldStateCheckpoint,
    model: str,
    scene_count: int,
    system_prompt_text: str,
    user_prompt_text: str,
    total_pages: int = 1,
    quotes: list[dict[str, str | None]] | None = None,
) -> str:
    """Generate story bible via LLM. Returns the raw text output (not parsed)."""
    client = _build_instructor_client(model)

    system_prompt = system_prompt_text
    user_prompt = user_prompt_text

    # Request raw text completion directly; no structured response model is required.
    response = client.chat.completions.create(
        model=model,
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    
    return response.choices[0].message.content or ""


def create_story_bible(
    raw_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/01_raw_text.json"),
    entities_checkpoint_path: Path = Path("campaigns/<campaign>/<episode>/v001/02_entities.json"),
    output_path: Path = Path("campaigns/<campaign>/<episode>/v001/02_5_story_bible.txt"),
    *,
    system_prompt_text: str,
    user_prompt_text: str,
    model: str = DEFAULT_MODEL,
    scene_count: int = 6,
    total_pages: int = 1,
    generator: StoryBibleGenerator | None = None,
) -> StoryBibleCheckpoint:
    """Generate a story bible checkpoint from raw text and entities.
    
    Args:
        raw_checkpoint_path: Path to 01_raw_text.json
        entities_checkpoint_path: Path to 02_entities.json
        output_path: Path where story_bible.txt will be written
        system_prompt_text: Fully rendered system prompt text to send to the model
        user_prompt_text: Fully rendered user prompt text to send to the model
        model: LLM model name
        scene_count: Target number of scenes to generate
        total_pages: Total number of pages to distribute scenes across (default: 1)
        generator: Optional custom generator function for testing
        
    Returns:
        StoryBibleCheckpoint with story_bible text and metadata
    """
    if scene_count < 1:
        raise ValueError("scene_count must be >= 1")

    raw = RawTextCheckpoint.model_validate_json(raw_checkpoint_path.read_text(encoding="utf-8"))
    world = WorldStateCheckpoint.model_validate_json(
        entities_checkpoint_path.read_text(encoding="utf-8")
    )
    normalized_content = _normalize_aliases_in_text(raw.content, world)

    quotes_list: list[dict[str, str | None]] = []
    for quote in raw.quotes:
        text = quote.text.strip() if quote.text else ""
        if text:
            attribution = quote.attribution or "Unknown attribution"
            quotes_list.append({"text": text, "attribution": attribution})

    generation_errors: list[str] = []

    if generator is not None:
        story_bible_text = generator(normalized_content, world, model, scene_count)
    else:
        try:
            story_bible_text = _generate_with_ollama(
                normalized_content,
                world,
                model,
                scene_count,
                system_prompt_text,
                user_prompt_text,
                quotes=quotes_list,
            )
        except Exception as exc:
            generation_errors.append(f"Story bible generation failed: {exc}")
            raise RuntimeError(f"Generation failed: {exc}") from exc

    if not story_bible_text or not story_bible_text.strip():
        generation_errors.append("Generated story bible is empty.")
        raise RuntimeError("Generated story bible text is empty")

    story_bible_text = story_bible_text.strip()
    try:
        derived_scene_count = validate_full_story_bible_scenes(story_bible_text)
    except ValueError as exc:
        generation_errors.append(str(exc))
        raise RuntimeError(str(exc)) from exc

    checkpoint = StoryBibleCheckpoint(
        url=raw.url,
        title=raw.title,
        author=raw.author,
        model=model,
        scene_count=derived_scene_count,
        story_bible=story_bible_text,
        generation_errors=generation_errors,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    write_story_bible(output_path, story_bible_text)
    return checkpoint


def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a story bible checkpoint for comic adaptation."
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
        help="Output story bible checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/02_5_story_bible.txt)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Ollama model name",
    )
    parser.add_argument(
        "--scene-count",
        type=int,
        default=6,
        help="Target number of scenes to generate",
    )
    args = parser.parse_args()

    raw = RawTextCheckpoint.model_validate_json(Path(args.raw_input).read_text(encoding="utf-8"))
    world = WorldStateCheckpoint.model_validate_json(
        Path(args.entities_input).read_text(encoding="utf-8")
    )
    quotes_list: list[dict[str, str | None]] = []
    for quote in raw.quotes:
        text = quote.text.strip() if quote.text else ""
        if text:
            attribution = quote.attribution or "Unknown attribution"
            quotes_list.append({"text": text, "attribution": attribution})

    template_vars = {
        "title": world.title or "Untitled story",
        "panel_count": args.scene_count,
        "scene_count": args.scene_count,
        "total_pages": 1,
        "entities_context": _format_entities_for_prompt(world),
        "story_text": raw.content,
        "reference_quotes": _format_quotes_for_prompt(quotes_list),
    }
    system_prompt_text = render_prompt_template(
        MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
        **template_vars,
    )
    user_prompt_text = render_prompt_template(
        MASTER_BEATER_USER_PROMPT_FILENAME,
        **template_vars,
    )

    checkpoint = create_story_bible(
        raw_checkpoint_path=Path(args.raw_input),
        entities_checkpoint_path=Path(args.entities_input),
        output_path=Path(args.output),
        model=args.model,
        scene_count=args.scene_count,
        system_prompt_text=system_prompt_text,
        user_prompt_text=user_prompt_text,
    )
    print(f"Story bible created with {checkpoint.scene_count} scenes: {args.output}")


if __name__ == "__main__":
    _run_cli()
