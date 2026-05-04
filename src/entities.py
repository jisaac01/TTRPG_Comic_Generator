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
    demeanor: str = Field(min_length=1)


class Location(BaseModel):
    name: str = Field(min_length=1)
    appearance: str = Field(min_length=1)


class Quote(BaseModel):
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)


class StoryBeat(BaseModel):
    index: int = Field(ge=1)
    text: str = Field(min_length=1)
    quotes: list[Quote] = Field(default_factory=list)


class WorldStateCheckpoint(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    model: str
    characters: list[Character]
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


def _build_characters(raw: RawTextCheckpoint) -> list[Character]:
    """Merge player_characters and npcs into a deduplicated Character list.

    Player characters are listed first. Duplicates are resolved by keeping
    the first occurrence (case-insensitive name match). Missing descriptions
    receive a placeholder so the downstream validator always sees a non-empty
    string.
    """
    candidates: list[tuple[str, str | None]] = [
        (item.name, item.description) for item in raw.player_characters
    ]
    candidates.extend((item.name, item.description) for item in raw.npcs)

    characters: list[Character] = []
    for name, description in _dedupe_by_name(candidates):
        characters.append(
            Character(
                name=name,
                description=(description or "").strip() or "No source description provided.",
                demeanor="Unknown",
            )
        )
    return characters


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

    Falls back to a single beat wrapping the full recap content when the
    outline is empty.
    """
    beats: list[StoryBeat] = []
    for idx, text in enumerate(raw.outline, start=1):
        cleaned = " ".join(text.split()).strip()
        if cleaned:
            beats.append(StoryBeat(index=idx, text=cleaned, quotes=[]))

    if beats:
        return beats

    return [StoryBeat(index=1, text=" ".join(raw.content.split()), quotes=[])]


def build_entities_from_raw(
    raw_checkpoint_path: Path,
    output_path: Path,
    model_label: str = "scraper-direct",
) -> WorldStateCheckpoint:
    """Build a WorldStateCheckpoint deterministically from scraped structured data.

    Merges player_characters and npcs into a unified characters list (deduped
    by name), maps locations, converts outline entries to beats, and attaches
    quotes from the scraped quotes section to the first beat.

    No LLM is involved. All data comes directly from the fields already
    extracted by the scraper.
    """
    raw = RawTextCheckpoint.model_validate_json(
        raw_checkpoint_path.read_text(encoding="utf-8")
    )

    characters = _build_characters(raw)
    locations = _build_locations(raw)
    beats = _build_beats(raw)

    if raw.quotes and beats:
        quotes: list[Quote] = []
        for scraped_quote in raw.quotes:
            text = " ".join(scraped_quote.text.split()).strip()
            if not text:
                continue
            speaker = (scraped_quote.attribution or "Unknown").strip() or "Unknown"
            quotes.append(Quote(speaker=speaker, text=text))
        if quotes:
            beats[0] = StoryBeat(
                index=beats[0].index,
                text=beats[0].text,
                quotes=quotes,
            )

    checkpoint = WorldStateCheckpoint(
        url=raw.url,
        title=raw.title,
        author=raw.author,
        model=model_label,
        characters=characters,
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
