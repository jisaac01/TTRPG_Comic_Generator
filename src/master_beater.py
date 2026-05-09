from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from entities import StoryBeat, WorldStateCheckpoint
from prompt_templates import (
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    render_prompt_template,
)
from scraper import RawTextCheckpoint


class StoryBibleCheckpoint(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    scene_count: int = Field(ge=1)
    story_bible: str = Field(min_length=1, description="Text-only narrative scene breakdown")
    generation_errors: list[str] = Field(default_factory=list)
    created_at: str


StoryBibleGenerator = Callable[[str, WorldStateCheckpoint, str, int], str]


def _format_entities_for_prompt(world: WorldStateCheckpoint) -> str:
    pc_lines = "\n".join(
        f"- {character.name}: {character.description}" for character in world.player_characters
    )
    npc_lines = "\n".join(
        f"- {character.name}: {character.description}" for character in world.npcs
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


def _format_quotes_for_prompt(quotes: list[dict[str, str | None]] | None = None) -> str:
    quote_lines: list[str] = []
    for quote in quotes or []:
        text = quote.get("text", "").strip() if isinstance(quote, dict) else ""
        attribution = quote.get("attribution", "").strip() if isinstance(quote, dict) else ""
        if text:
            speaker = attribution or "Unknown"
            quote_lines.append(f"- {speaker}: \"{text}\"")
    return "\n".join(quote_lines) or "- none"


def _build_instructor_client():
    from openai import OpenAI

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")
    return OpenAI(base_url=base_url, api_key=api_key)


def _generate_with_ollama(
    content: str,
    world: WorldStateCheckpoint,
    model: str,
    scene_count: int,
    quotes: list[dict[str, str | None]] | None = None,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> str:
    """Generate story bible via LLM. Returns the raw text output (not parsed)."""
    client = _build_instructor_client()

    template_vars = {
        "title": world.title or "Untitled story",
        "panel_count": scene_count,
        "scene_count": scene_count,
        "entities_context": _format_entities_for_prompt(world),
        "story_text": content,
        "reference_quotes": _format_quotes_for_prompt(quotes),
    }

    system_prompt = render_prompt_template(
        MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
        template_path=system_prompt_path,
        **template_vars,
    )
    user_prompt = render_prompt_template(
        MASTER_BEATER_USER_PROMPT_FILENAME,
        template_path=user_prompt_path,
        **template_vars,
    )

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
    model: str = "qwen3:8b",
    scene_count: int = 6,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
    generator: StoryBibleGenerator | None = None,
) -> StoryBibleCheckpoint:
    """Generate a story bible checkpoint from raw text and entities.
    
    Args:
        raw_checkpoint_path: Path to 01_raw_text.json
        entities_checkpoint_path: Path to 02_entities.json
        output_path: Path where story_bible.txt will be written
        model: LLM model name
        scene_count: Target number of scenes to generate
        system_prompt_path: Optional override path for system prompt
        user_prompt_path: Optional override path for user prompt
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

    quotes_list: list[dict[str, str | None]] = []
    for quote in raw.quotes:
        text = quote.text.strip() if quote.text else ""
        if text:
            attribution = quote.attribution or "Unknown attribution"
            quotes_list.append({"text": text, "attribution": attribution})

    generation_errors: list[str] = []

    if generator is not None:
        story_bible_text = generator(raw.content, world, model, scene_count)
    else:
        try:
            story_bible_text = _generate_with_ollama(
                raw.content,
                world,
                model,
                scene_count,
                quotes=quotes_list,
                system_prompt_path=system_prompt_path,
                user_prompt_path=user_prompt_path,
            )
        except Exception as exc:
            generation_errors.append(f"Story bible generation failed: {exc}")
            raise RuntimeError(f"Generation failed: {exc}") from exc

    if not story_bible_text or not story_bible_text.strip():
        generation_errors.append("Generated story bible is empty.")
        raise RuntimeError("Generated story bible text is empty")

    checkpoint = StoryBibleCheckpoint(
        url=raw.url,
        title=raw.title,
        author=raw.author,
        model=model,
        scene_count=scene_count,
        story_bible=story_bible_text,
        generation_errors=generation_errors,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Write metadata to JSON; the actual story_bible text is also accessible as story_bible field
    output_path.write_text(
        json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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
        help="Output story bible checkpoint path (e.g. campaigns/<campaign>/<episode>/v001/02_5_story_bible.json)",
    )
    parser.add_argument(
        "--model",
        default="qwen3:8b",
        help="Ollama model name",
    )
    parser.add_argument(
        "--scene-count",
        type=int,
        default=6,
        help="Target number of scenes to generate",
    )
    args = parser.parse_args()
    checkpoint = create_story_bible(
        raw_checkpoint_path=Path(args.raw_input),
        entities_checkpoint_path=Path(args.entities_input),
        output_path=Path(args.output),
        model=args.model,
        scene_count=args.scene_count,
    )
    print(f"Story bible created with {checkpoint.scene_count} scenes: {args.output}")


if __name__ == "__main__":
    _run_cli()
