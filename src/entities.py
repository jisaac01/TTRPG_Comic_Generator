from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from scraper import RawTextCheckpoint


# ---------------------------------------------------------------------------
# World-state data models
# ---------------------------------------------------------------------------


class Character(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Location(BaseModel):
    name: str = Field(min_length=1)
    appearance: str = Field(min_length=1)


class StoryBeat(BaseModel):
    index: int = Field(ge=1)
    beat: str = Field(min_length=1)
    highlights: list[str] = Field(min_length=1)


class WorldStateCheckpoint(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    player_characters: list[Character]
    npcs: list[Character]
    locations: list[Location]
    beats: list[StoryBeat]
    analyzed_at: str


# ---------------------------------------------------------------------------
# Entity builders
# ---------------------------------------------------------------------------


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _dedupe_by_name(
    items: list[tuple[str, str | None]],
) -> list[tuple[str, str | None]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str | None]] = []
    for name, description in items:
        key = _normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((name.strip(), description))
    return deduped


def _build_player_characters(raw: RawTextCheckpoint) -> list[Character]:
    """Build a deduplicated list of player characters from scraped data."""
    characters: list[Character] = []
    for name, description in _dedupe_by_name(
        [(item.name, item.description) for item in raw.player_characters]
    ):
        characters.append(
            Character(
                name=name,
                description=(description or "").strip() or "No source description provided.",
            )
        )
    return characters


def _build_npcs(
    raw: RawTextCheckpoint,
    pc_names: set[str],
) -> list[Character]:
    """Build a deduplicated NPC list, skipping any name already in pc_names."""
    npcs: list[Character] = []
    for name, description in _dedupe_by_name(
        [(item.name, item.description) for item in raw.npcs]
    ):
        if _normalize_name(name) in pc_names:
            continue
        npcs.append(
            Character(
                name=name,
                description=(description or "").strip() or "No source description provided.",
            )
        )
    return npcs


def _build_locations(raw: RawTextCheckpoint) -> list[Location]:
    """Map scraped location entities to Location models."""
    locations: list[Location] = []
    for name, description in _dedupe_by_name(
        [(item.name, item.description) for item in raw.locations]
    ):
        locations.append(
            Location(
                name=name,
                appearance=(description or "").strip() or "No source appearance provided.",
            )
        )
    return locations


def _build_beats(raw: RawTextCheckpoint) -> list[StoryBeat]:
    """Convert outline entries to StoryBeats.

    Outline items starting with '### ' begin a new beat; all subsequent
    non-heading items are detail lines (highlights) under that beat.

    Falls back to a single beat wrapping the full recap content when the
    outline is empty or contains no headings.
    """
    beats: list[StoryBeat] = []
    current_header: str | None = None
    current_details: list[str] = []

    def _flush(index: int) -> None:
        if current_header is None:
            return
        highlights = current_details if current_details else [current_header]
        beats.append(StoryBeat(index=index, beat=current_header, highlights=highlights))

    beat_index = 1
    for item in raw.outline:
        cleaned = " ".join(item.split()).strip()
        if not cleaned:
            continue
        if cleaned.startswith("### "):
            _flush(beat_index)
            if current_header is not None:
                beat_index += 1
            current_header = cleaned[4:].strip()
            current_details = []
        else:
            current_details.append(cleaned)

    _flush(beat_index)

    if beats:
        return beats

    fallback_text = " ".join(raw.content.split())
    return [StoryBeat(index=1, beat=fallback_text, highlights=[fallback_text])]


def build_entities_from_raw(
    raw_checkpoint_path: Path,
    output_path: Path,
    model_label: str = "scraper-direct",
) -> WorldStateCheckpoint:
    """Build a WorldStateCheckpoint deterministically from scraped structured data.

    Keeps player_characters and npcs as separate lists (PCs take priority when
    the same name appears in both). Maps locations and outline entries to beats.

    No LLM is involved. All data comes directly from the fields already
    extracted by the scraper.
    """
    raw = RawTextCheckpoint.model_validate_json(
        raw_checkpoint_path.read_text(encoding="utf-8")
    )

    player_characters = _build_player_characters(raw)
    pc_names = {_normalize_name(c.name) for c in player_characters}
    npcs = _build_npcs(raw, pc_names)
    locations = _build_locations(raw)
    beats = _build_beats(raw)

    checkpoint = WorldStateCheckpoint(
        url=raw.url,
        title=raw.title,
        author=raw.author,
        model=model_label,
        player_characters=player_characters,
        npcs=npcs,
        locations=locations,
        beats=beats,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(checkpoint.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return checkpoint
