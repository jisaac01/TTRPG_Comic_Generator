from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_client import build_openai_client
from model_defaults import DEFAULT_MODEL
from prompt_templates import (
    ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME,
    ENTITIES_CONTINUITY_USER_PROMPT_FILENAME,
    render_prompt_template,
)
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
    age: str | None = None
    sex: str | None = None
    build: str | None = None
    height: str | None = None
    hair: str | None = None
    eyes: str | None = None
    skin: str | None = None
    distinguishing_marks: str | None = None
    physical_description: str | None = None
    clothing_armor: str | None = None
    weapons: str | None = None
    distinctive_props: str | None = None
    character_quirks: str | None = None
    aliases: list[str] = Field(default_factory=list)


# Placeholder values that should not appear in prompt text.
_PLACEHOLDER_DETAIL_VALUES = frozenset(
    {
        "none",
        "none.",
        "unknown",
        "unknown.",
        "n/a",
        "na",
        "null",
        "null.",
    }
)

CharacterAudience = Literal["visual", "narrative"]

# Portrait atoms rendered as labeled body lines when present.
_VISUAL_BODY_FIELDS: tuple[tuple[str, str], ...] = (
    ("Physical", "physical_description"),
    ("Build", "build"),
    ("Height", "height"),
    ("Hair", "hair"),
    ("Eyes", "eyes"),
    ("Skin", "skin"),
    ("Marks", "distinguishing_marks"),
    ("Clothing/Armor", "clothing_armor"),
    ("Weapons", "weapons"),
    ("Props", "distinctive_props"),
)


def clean_character_detail(value: str | None) -> str | None:
    """Return a stripped detail value, or None when empty / placeholder."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.casefold() in _PLACEHOLDER_DETAIL_VALUES:
        return None
    return stripped


def is_usable_character_detail(value: str | None) -> bool:
    """Return True when a character field is present and not a placeholder."""
    return clean_character_detail(value) is not None


def _format_character_header(character: Character) -> str:
    """Name with race, class, age, and sex when present."""
    identity_parts: list[str] = []
    race = clean_character_detail(character.race)
    class_name = clean_character_detail(character.class_name)
    age = clean_character_detail(character.age)
    sex = clean_character_detail(character.sex)
    if race:
        identity_parts.append(f"Race: {race}")
    if class_name:
        identity_parts.append(f"Class: {class_name}")
    if age:
        identity_parts.append(f"age {age}")
    if sex:
        identity_parts.append(sex)

    if identity_parts:
        return f"{character.name} ({'; '.join(identity_parts)}):"
    return f"{character.name}:"


def _format_visual_body_lines(character: Character) -> list[str]:
    """Labeled visual fields that are present and non-placeholder."""
    body_lines: list[str] = []
    for label, attr in _VISUAL_BODY_FIELDS:
        cleaned = clean_character_detail(getattr(character, attr))
        if cleaned:
            body_lines.append(f"{label}: {cleaned}")
    return body_lines


def format_character_details(
    character: Character,
    *,
    bullet: bool = False,
    audience: CharacterAudience = "narrative",
) -> str:
    """Format one character for LLM or image prompts.

    audience=\"visual\" (image prompts): identity header + present portrait
    atoms, clothing, weapons, and props. Omits narrative description and
    personality quirks when any visual content exists.

    audience=\"narrative\" (script / architect): identity header, role description,
    visual summary, and character_quirks.

    Placeholder values (None, Unknown, etc.) are omitted. Falls back to
    description alone when no specialized visual body fields are present.
    """
    header = _format_character_header(character)
    body_lines = _format_visual_body_lines(character)

    if audience == "narrative":
        description = clean_character_detail(character.description)
        quirks = clean_character_detail(character.character_quirks)
        narrative_lines: list[str] = []
        if description:
            narrative_lines.append(description)
        narrative_lines.extend(body_lines)
        if quirks:
            narrative_lines.append(f"Quirks: {quirks}")
        body_lines = narrative_lines
    elif not body_lines:
        description = clean_character_detail(character.description)
        if description:
            body_lines.append(description)

    lines = [header, *body_lines]
    if bullet:
        return "\n".join([f"- {lines[0]}"] + [f"  {line}" for line in lines[1:]])
    return "\n".join(lines)


class Location(BaseModel):
    name: str = Field(min_length=1)
    appearance: str = Field(min_length=1)


class StoryBeat(BaseModel):
    index: int = Field(ge=1)
    beat: str = Field(min_length=1)
    highlights: list[str] = Field(min_length=1)


class ContinuityMergePayload(BaseModel):
    player_characters: list[Character] = Field(default_factory=list)
    npcs: list[Character] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    beats: list[StoryBeat] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    items: Sequence[tuple[str, str | None]],
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


def _merge_entities_with_llm(
    existing: WorldStateCheckpoint,
    incoming: WorldStateCheckpoint,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt_text: str | None = None,
    user_prompt_text: str | None = None,
) -> tuple[WorldStateCheckpoint, list[str]]:
    """Use the configured LLM to merge and enrich entity continuity data."""

    system_prompt = system_prompt_text or render_prompt_template(
        name=ENTITIES_CONTINUITY_SYSTEM_PROMPT_FILENAME
    )
    user_prompt = user_prompt_text or render_prompt_template(
        name=ENTITIES_CONTINUITY_USER_PROMPT_FILENAME,
        existing_entities_json=json.dumps(
            existing.model_dump(mode="json"), indent=2, ensure_ascii=False
        ),
        incoming_entities_json=json.dumps(
            incoming.model_dump(mode="json"), indent=2, ensure_ascii=False
        ),
    )

    client = build_openai_client(model)
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM continuity merge returned no content.")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM continuity merge returned invalid JSON.") from exc

    merge_payload = ContinuityMergePayload.model_validate(payload)
    merged_characters = merge_payload.player_characters or list(existing.player_characters)
    merged_npcs = merge_payload.npcs or list(existing.npcs)
    merged_locations = merge_payload.locations or list(existing.locations)
    merged_beats = merge_payload.beats or list(existing.beats)

    merged = WorldStateCheckpoint(
        url=existing.url or incoming.url,
        title=existing.title or incoming.title,
        author=existing.author or incoming.author,
        model=model,
        player_characters=merged_characters,
        npcs=merged_npcs,
        locations=merged_locations,
        beats=merged_beats,
        analyzed_at=existing.analyzed_at or incoming.analyzed_at,
    )

    return merged, merge_payload.warnings


def _latest_version_dir(episode_dir: Path) -> Path | None:
    version_dirs = sorted(
        (
            path
            for path in episode_dir.iterdir()
            if path.is_dir() and path.name.startswith("v") and path.name[1:].isdigit()
        ),
        key=lambda path: int(path.name[1:]),
        reverse=True,
    )
    return version_dirs[0] if version_dirs else None


def _previous_version_dir(episode_dir: Path, current_version_dir: Path) -> Path | None:
    version_dirs = sorted(
        (
            path
            for path in episode_dir.iterdir()
            if path.is_dir() and path.name.startswith("v") and path.name[1:].isdigit()
        ),
        key=lambda path: int(path.name[1:]),
    )
    try:
        current_index = version_dirs.index(current_version_dir)
    except ValueError:
        return None
    return version_dirs[current_index - 1] if current_index > 0 else None


def _episode_created_at(episode_dir: Path) -> datetime | None:
    meta_path = episode_dir / "episode_meta.json"
    if not meta_path.exists():
        return None
    try:
        created_at = json.loads(meta_path.read_text(encoding="utf-8")).get("created_at")
        return datetime.fromisoformat(created_at) if created_at else None
    except (TypeError, ValueError, OSError, json.JSONDecodeError):
        return None


def _find_previous_episode_dir(campaign_root: Path, episode_dir: Path) -> Path | None:
    current_created_at = _episode_created_at(episode_dir)
    if current_created_at is None:
        return None

    candidates = []
    for candidate in campaign_root.iterdir():
        if not candidate.is_dir() or candidate == episode_dir:
            continue
        created_at = _episode_created_at(candidate)
        if created_at is not None and created_at < current_created_at:
            candidates.append((created_at, candidate))

    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _resolve_bible_source(
    campaign_root: Path,
    version_dir: Path,
    incoming: WorldStateCheckpoint,
) -> WorldStateCheckpoint:
    bible_path = campaign_root / "entities_bible.json"
    if bible_path.exists():
        return WorldStateCheckpoint.model_validate_json(bible_path.read_text(encoding="utf-8"))

    current_episode_dir = version_dir.parent
    previous_version_dir = _previous_version_dir(current_episode_dir, version_dir)
    if previous_version_dir is not None:
        previous_entities_path = previous_version_dir / "02_entities.json"
        if previous_entities_path.exists():
            return WorldStateCheckpoint.model_validate_json(
                previous_entities_path.read_text(encoding="utf-8")
            )

    previous_episode_dir = _find_previous_episode_dir(campaign_root, current_episode_dir)
    if previous_episode_dir is not None:
        latest_previous_version_dir = _latest_version_dir(previous_episode_dir)
        if latest_previous_version_dir is not None:
            previous_episode_entities_path = latest_previous_version_dir / "02_entities.json"
            if previous_episode_entities_path.exists():
                return WorldStateCheckpoint.model_validate_json(
                    previous_episode_entities_path.read_text(encoding="utf-8")
                )

    return incoming


# Version-local full campaign cast after continuity merge (past + present).
ENTITIES_BIBLE_VERSION_FILENAME = "02_5_entities_bible.json"
# Episode cast only: names present in 02_entities.json, records enriched from the bible.
EPISODE_ENTITIES_FILENAME = "02_5_episode_entities.json"


def write_entities_bible(
    *,
    campaign_root: Path,
    version_dir: Path,
    entities_path: Path,
    system_prompt_path: Path | None = None,
    user_prompt_path: Path | None = None,
) -> tuple[Path, Path, WorldStateCheckpoint, list[str]]:
    """Create/update the campaign-root entities bible and the version-local copy.

    Source precedence is:
    1. the existing campaign bible itself,
    2. the previous version's 02_entities.json in the current episode,
    3. the latest 02_entities.json from the most recently created previous episode,
    4. the current version's entities checkpoint.

    """
    from prompt_saver import prepare_entities_continuity_prompts

    if not entities_path.exists():
        raise FileNotFoundError(f"Entities checkpoint not found at {entities_path}.")

    incoming = WorldStateCheckpoint.model_validate_json(
        entities_path.read_text(encoding="utf-8")
    )
    bible_path = campaign_root / "entities_bible.json"
    version_copy_path = version_dir / ENTITIES_BIBLE_VERSION_FILENAME

    existing = _resolve_bible_source(campaign_root, version_dir, incoming)
    system_prompt_text, user_prompt_text = prepare_entities_continuity_prompts(
        version_dir=version_dir,
        existing=existing,
        incoming=incoming,
        system_prompt_path=system_prompt_path,
        user_prompt_path=user_prompt_path,
    )
    merged, warnings = _merge_entities_with_llm(
        existing,
        incoming,
        system_prompt_text=system_prompt_text,
        user_prompt_text=user_prompt_text,
    )

    bible_path.parent.mkdir(parents=True, exist_ok=True)
    bible_path.write_text(
        json.dumps(merged.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    version_copy_path.parent.mkdir(parents=True, exist_ok=True)
    version_copy_path.write_text(
        json.dumps(merged.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return bible_path, version_copy_path, merged, warnings


def _character_identity_keys(character: Character) -> set[str]:
    keys = {_normalize_name(character.name)}
    for alias in character.aliases or []:
        alias_name = alias.strip()
        if alias_name:
            keys.add(_normalize_name(alias_name))
    return {key for key in keys if key}


def _index_characters_by_identity(
    characters: list[Character],
) -> dict[str, Character]:
    index: dict[str, Character] = {}
    for character in characters:
        for key in _character_identity_keys(character):
            index[key] = character
    return index


def _resolve_character_from_bible(
    episode_character: Character,
    *,
    bible_pcs: dict[str, Character],
    bible_npcs: dict[str, Character],
    default_role: str,
) -> tuple[Character, str]:
    """Return (bible_or_episode_character, 'pc'|'npc')."""
    for key in _character_identity_keys(episode_character):
        if key in bible_pcs:
            return bible_pcs[key], "pc"
        if key in bible_npcs:
            return bible_npcs[key], "npc"
    return episode_character, default_role


def project_episode_entities(
    episode: WorldStateCheckpoint,
    bible: WorldStateCheckpoint,
) -> WorldStateCheckpoint:
    """Build an episode-scoped world state from scraped entities + bible records.

    Membership comes from the episode checkpoint (who appears this session).
    When a name/alias matches the bible, the bible's canonical character record
    is used (name, aliases, descriptions). Locations work the same way by name.
    Beats always stay episode-local. Characters only in the campaign bible are
    excluded so later stages cannot invent absent cast members.
    """
    bible_pcs = _index_characters_by_identity(bible.player_characters)
    bible_npcs = _index_characters_by_identity(bible.npcs)
    bible_locations = {
        _normalize_name(location.name): location for location in bible.locations
    }

    player_characters: list[Character] = []
    npcs: list[Character] = []
    seen_keys: set[str] = set()

    def _add(character: Character, role: str) -> None:
        identity = _character_identity_keys(character)
        if identity & seen_keys:
            return
        seen_keys.update(identity)
        if role == "pc":
            player_characters.append(character)
        else:
            npcs.append(character)

    for character in episode.player_characters:
        resolved, role = _resolve_character_from_bible(
            character,
            bible_pcs=bible_pcs,
            bible_npcs=bible_npcs,
            default_role="pc",
        )
        _add(resolved, role)

    for character in episode.npcs:
        resolved, role = _resolve_character_from_bible(
            character,
            bible_pcs=bible_pcs,
            bible_npcs=bible_npcs,
            default_role="npc",
        )
        _add(resolved, role)

    locations: list[Location] = []
    seen_locations: set[str] = set()
    for location in episode.locations:
        key = _normalize_name(location.name)
        if not key or key in seen_locations:
            continue
        seen_locations.add(key)
        locations.append(bible_locations.get(key, location))

    return WorldStateCheckpoint(
        url=episode.url,
        title=episode.title,
        author=episode.author,
        model=episode.model,
        player_characters=player_characters,
        npcs=npcs,
        locations=locations,
        beats=list(episode.beats),
        analyzed_at=episode.analyzed_at,
    )


def write_episode_entities(
    *,
    entities_path: Path,
    bible: WorldStateCheckpoint,
    output_path: Path,
) -> WorldStateCheckpoint:
    """Project episode entities through the bible and write 02_5_episode_entities.json."""
    if not entities_path.exists():
        raise FileNotFoundError(f"Entities checkpoint not found at {entities_path}.")

    episode = WorldStateCheckpoint.model_validate_json(
        entities_path.read_text(encoding="utf-8")
    )
    projected = project_episode_entities(episode, bible)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(projected.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return projected


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
