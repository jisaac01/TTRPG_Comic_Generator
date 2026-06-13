from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from scraper import RawTextCheckpoint


# ---------------------------------------------------------------------------
# World-state data models
# ---------------------------------------------------------------------------


class Character(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    class_name: str | None = Field(
        default=None,
        validation_alias="class",
        serialization_alias="class",
    )
    race: str | None = None
    physical_description: str | None = None
    aliases: list[str] = Field(default_factory=list)


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


def _similarity_score(left: str, right: str) -> float:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
    if ratio >= 0.75:
        return ratio
    return 0.0


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


def merge_entities_for_bible(
    existing: WorldStateCheckpoint,
    incoming: WorldStateCheckpoint,
) -> tuple[WorldStateCheckpoint, list[str]]:
    """Merge two world-state checkpoints into a canonical bible view.

    The merge is deterministic for exact-name matches and conservative for
    near-duplicates. It preserves existing richer values when conflicts arise,
    unions aliases, and emits warnings for contradictory descriptions and
    ambiguous similar names.
    """

    merged_characters: list[Character] = []
    warnings: list[str] = []

    existing_by_name = { _normalize_name(character.name): character for character in existing.player_characters }
    incoming_by_name = { _normalize_name(character.name): character for character in incoming.player_characters }

    merged_characters = list(existing.player_characters)

    for incoming_character in incoming.player_characters:
        key = _normalize_name(incoming_character.name)
        existing_character = existing_by_name.get(key)
        if existing_character is not None:
            merged_description = incoming_character.description
            if len(incoming_character.description.strip()) < len(existing_character.description.strip()):
                merged_description = existing_character.description

            if incoming_character.description.strip().lower() != existing_character.description.strip().lower():
                warnings.append(
                    f"Description conflict for {existing_character.name}: keeping the richer canonical description."
                )

            merged_aliases = list(dict.fromkeys([*existing_character.aliases, *incoming_character.aliases]))
            merged_character = Character(
                name=existing_character.name,
                description=merged_description.strip() or existing_character.description,
                class_name=existing_character.class_name or incoming_character.class_name,
                race=existing_character.race or incoming_character.race,
                physical_description=existing_character.physical_description or incoming_character.physical_description,
                aliases=merged_aliases,
            )
            merged_characters = [
                merged_character if _normalize_name(character.name) == key else character
                for character in merged_characters
            ]
            continue

        similar = [
            character
            for character in existing.player_characters
            if _similarity_score(character.name, incoming_character.name) >= 0.75
        ]
        if similar:
            warnings.append(
                f"Ambiguous similar name for {incoming_character.name}: matched {', '.join(character.name for character in similar)}."
            )

        merged_characters.append(
            Character(
                name=incoming_character.name,
                description=incoming_character.description,
                class_name=incoming_character.class_name,
                race=incoming_character.race,
                physical_description=incoming_character.physical_description,
                aliases=list(dict.fromkeys(incoming_character.aliases)),
            )
        )

    merged = WorldStateCheckpoint(
        url=existing.url,
        title=existing.title or incoming.title,
        author=existing.author or incoming.author,
        model=existing.model or incoming.model,
        player_characters=merged_characters,
        npcs=list(existing.npcs) + list(incoming.npcs),
        locations=list(existing.locations) + list(incoming.locations),
        beats=list(existing.beats) + list(incoming.beats),
        analyzed_at=existing.analyzed_at or incoming.analyzed_at,
    )

    return merged, warnings


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
