import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import entities
from entities import (
    Character,
    Location,
    StoryBeat,
    WorldStateCheckpoint,
    _build_beats,
    write_entities_bible,
    write_episode_entities,
    project_episode_entities,
    _build_npcs,
    _build_player_characters,
    _build_locations,
    _dedupe_by_name,
    _normalize_name,
    build_entities_from_raw,
    EPISODE_ENTITIES_FILENAME,
)
from scraper import RawTextCheckpoint, ScrapedEntity, ScrapedQuote


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_continuity_merge(existing, incoming, model="test-model"):
    """Lightweight simulation of LLM continuity merge for character fields only.
    Provides deterministic name-priority + alias union behavior so that existing
    write_entities_bible tests can assert on the resulting checkpoint and written files.
    Locations and beats are passed through from existing (tests that need populated
    locs/beats for write use local patches instead).
    We do not simulate LLM dedup or fuzzy name matching here; those rules live in the
    prompts and the real _merge_entities_with_llm (tested via direct payload delegation).
    """
    merged = existing.model_copy(deep=True)

    if not incoming.player_characters:
        return merged, []

    existing_by_name = {_normalize_name(character.name): character for character in existing.player_characters}
    merged_characters = []
    for incoming_character in incoming.player_characters:
        existing_character = existing_by_name.get(_normalize_name(incoming_character.name))
        if existing_character is None:
            merged_characters.append(incoming_character)
            continue

        description = existing_character.description
        if len(incoming_character.description) > len(existing_character.description):
            description = incoming_character.description

        merged_characters.append(
            Character(
                name=existing_character.name,
                description=description,
                class_name=existing_character.class_name or incoming_character.class_name,
                race=existing_character.race or incoming_character.race,
                physical_description=existing_character.physical_description or incoming_character.physical_description,
                aliases=list(dict.fromkeys([*existing_character.aliases, *incoming_character.aliases])),
            )
        )

    merged.player_characters = merged_characters
    # For locs/beats, tests exercising them use local patches returning explicit results.
    # Keep from existing to avoid polluting shared tests that expect empty.
    return merged, ["description continuity warning"]


def _make_raw(
    *,
    content: str = "The party crossed the marsh.",
    outline: list[str] | None = None,
    player_characters: list[ScrapedEntity] | None = None,
    npcs: list[ScrapedEntity] | None = None,
    locations: list[ScrapedEntity] | None = None,
    quotes: list[ScrapedQuote] | None = None,
) -> RawTextCheckpoint:
    return RawTextCheckpoint(
        url="https://example.test/story",
        title="Test Story",
        author="GM",
        content=content,
        source_selector="div.story",
        scraped_at="2026-05-04T00:00:00+00:00",
        outline=outline or [],
        player_characters=player_characters or [],
        npcs=npcs or [],
        locations=locations or [],
        quotes=quotes or [],
    )


# ---------------------------------------------------------------------------
# _dedupe_by_name
# ---------------------------------------------------------------------------


def test_dedupe_keeps_first_occurrence():
    items = [("Del", "druid"), ("del", "another"), ("Vendetta", "vampire")]
    result = _dedupe_by_name(items)
    assert [name for name, _ in result] == ["Del", "Vendetta"]


def test_dedupe_strips_whitespace_from_name():
    items = [("  Del  ", "druid"), ("DEL", "other")]
    result = _dedupe_by_name(items)
    assert result == [("Del", "druid")]


def test_dedupe_drops_empty_names():
    items = [("", "nothing"), ("Del", "druid")]
    result = _dedupe_by_name(items)
    assert [name for name, _ in result] == ["Del"]


# ---------------------------------------------------------------------------
# _build_player_characters
# ---------------------------------------------------------------------------


def test_build_player_characters_returns_only_pcs():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="druid")],
        npcs=[ScrapedEntity(name="Merelda", description="witch")],
    )
    chars = _build_player_characters(raw)
    assert [c.name for c in chars] == ["Del"]


def test_build_player_characters_deduplicates():
    raw = _make_raw(
        player_characters=[
            ScrapedEntity(name="Del", description="first"),
            ScrapedEntity(name="del", description="duplicate"),
        ],
    )
    chars = _build_player_characters(raw)
    assert len(chars) == 1
    assert chars[0].description == "first"


def test_build_player_characters_fallback_description_when_none():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description=None)],
    )
    chars = _build_player_characters(raw)
    assert chars[0].description == "No source description provided."


def test_build_player_characters_fallback_description_when_whitespace_only():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="   ")],
    )
    chars = _build_player_characters(raw)
    assert chars[0].description == "No source description provided."


def test_build_player_characters_exposes_optional_continuity_fields_with_defaults():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="A druid")]
    )
    chars = _build_player_characters(raw)
    assert chars[0].model_dump() == {
        "name": "Del",
        "description": "A druid",
        "class_name": None,
        "race": None,
        "physical_description": None,
        "clothing_armor": None,
        "weapons": None,
        "character_quirks": None,
        "aliases": [],
    }


def test_character_accepts_alias_field_names_for_continuity_metadata():
    char = Character.model_validate(
        {
            "name": "Wulf",
            "description": "A sharp-eyed sailor.",
            "class": "Ranger",
            "race": "Human",
            "physical_description": "Tall and weather-beaten, with a scarred jaw and salt-stiff beard.",
            "clothing_armor": "Oilskin coat over scale-mail cuirass.",
            "weapons": "Cutlass and hand crossbow.",
            "character_quirks": "Always taps his cutlass on the deck before speaking.",
            "aliases": ["Wolf"],
        }
    )

    assert char.class_name == "Ranger"
    assert char.race == "Human"
    assert char.physical_description == "Tall and weather-beaten, with a scarred jaw and salt-stiff beard."
    assert char.clothing_armor == "Oilskin coat over scale-mail cuirass."
    assert char.weapons == "Cutlass and hand crossbow."
    assert char.character_quirks == "Always taps his cutlass on the deck before speaking."
    assert char.aliases == ["Wolf"]


def test_build_player_characters_empty_when_no_entities():
    raw = _make_raw()
    assert _build_player_characters(raw) == []


# ---------------------------------------------------------------------------
# _build_npcs
# ---------------------------------------------------------------------------


def test_build_npcs_returns_only_npcs():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="druid")],
        npcs=[ScrapedEntity(name="Merelda", description="witch")],
    )
    npcs = _build_npcs(raw, set())
    assert [c.name for c in npcs] == ["Merelda"]


def test_build_npcs_skips_names_already_in_pc_names():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="PC description")],
        npcs=[ScrapedEntity(name="del", description="NPC description")],
    )
    npcs = _build_npcs(raw, {"del"})
    assert npcs == []


def test_build_npcs_fallback_description_when_none():
    raw = _make_raw(
        npcs=[ScrapedEntity(name="Merelda", description=None)],
    )
    npcs = _build_npcs(raw, set())
    assert npcs[0].description == "No source description provided."


def test_build_npcs_empty_when_no_entities():
    raw = _make_raw()
    assert _build_npcs(raw, set()) == []


# ---------------------------------------------------------------------------
# _build_locations
# ---------------------------------------------------------------------------


def test_build_locations_maps_description_to_appearance():
    raw = _make_raw(
        locations=[ScrapedEntity(name="Dreadmarsh", description="A vast swamp.")]
    )
    locs = _build_locations(raw)
    assert locs[0].name == "Dreadmarsh"
    assert locs[0].appearance == "A vast swamp."


def test_build_locations_fallback_appearance_when_none():
    raw = _make_raw(
        locations=[ScrapedEntity(name="Dreadmarsh", description=None)]
    )
    locs = _build_locations(raw)
    assert locs[0].appearance == "No source appearance provided."


def test_build_locations_deduplicates():
    raw = _make_raw(
        locations=[
            ScrapedEntity(name="Marsh", description="foggy"),
            ScrapedEntity(name="marsh", description="other"),
        ]
    )
    locs = _build_locations(raw)
    assert len(locs) == 1
    assert locs[0].appearance == "foggy"


def test_build_locations_empty_when_no_entities():
    raw = _make_raw()
    assert _build_locations(raw) == []


# ---------------------------------------------------------------------------
# _build_beats
# ---------------------------------------------------------------------------


def test_build_beats_uses_outline_entries():
    raw = _make_raw(outline=["### Beat one", "### Beat two", "### Beat three"])
    beats = _build_beats(raw)
    assert [b.index for b in beats] == [1, 2, 3]
    assert [b.beat for b in beats] == ["Beat one", "Beat two", "Beat three"]
    assert all(b.highlights == [b.beat] for b in beats)


def test_build_beats_skips_blank_outline_entries():
    raw = _make_raw(outline=["### Beat one", "  ", "### Beat three"])
    beats = _build_beats(raw)
    assert len(beats) == 2
    assert beats[0].beat == "Beat one"
    assert beats[1].beat == "Beat three"


def test_build_beats_falls_back_to_content_when_outline_empty():
    raw = _make_raw(content="Del crossed the marsh.", outline=[])
    beats = _build_beats(raw)
    assert len(beats) == 1
    assert beats[0].index == 1
    assert beats[0].beat == "Del crossed the marsh."
    assert beats[0].highlights == ["Del crossed the marsh."]


def test_build_beats_normalizes_content_whitespace():
    raw = _make_raw(content="Del  crossed\n\nthe   marsh.", outline=[])
    beats = _build_beats(raw)
    assert beats[0].beat == "Del crossed the marsh."
    assert beats[0].highlights == ["Del crossed the marsh."]


def test_build_beats_groups_details_under_heading():
    raw = _make_raw(outline=[
        "### The Curse Begins",
        "The party was afflicted.",
        "They must find five ingredients.",
        "### Into the Swamp",
        "They avoided the crocodile temple.",
    ])
    beats = _build_beats(raw)
    assert len(beats) == 2
    assert beats[0].beat == "The Curse Begins"
    assert beats[0].highlights == ["The party was afflicted.", "They must find five ingredients."]
    assert beats[1].beat == "Into the Swamp"
    assert beats[1].highlights == ["They avoided the crocodile temple."]


def test_build_beats_heading_only_no_details():
    raw = _make_raw(outline=["### Solo Beat"])
    beats = _build_beats(raw)
    assert len(beats) == 1
    assert beats[0].beat == "Solo Beat"
    assert beats[0].highlights == ["Solo Beat"]


def test_build_beats_falls_back_when_no_headings_in_outline():
    """Outline with no ### items produces no beats; falls back to content."""
    raw = _make_raw(content="Del crossed the marsh.", outline=["plain line"])
    beats = _build_beats(raw)
    assert len(beats) == 1
    assert beats[0].beat == "Del crossed the marsh."
    assert beats[0].highlights == ["Del crossed the marsh."]


# ---------------------------------------------------------------------------
# build_entities_from_raw (full integration)
# ---------------------------------------------------------------------------


def _write_raw(tmp_path: Path, raw: RawTextCheckpoint) -> Path:
    path = tmp_path / "01_raw_text.json"
    path.write_text(raw.model_dump_json(), encoding="utf-8")
    return path


def test_build_entities_from_raw_writes_valid_checkpoint(tmp_path):
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="A druid")],
        locations=[ScrapedEntity(name="Marsh", description="Foggy")],
        outline=["### The party departs"],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert output_path.exists()
    assert checkpoint.model == "scraper-direct"
    assert checkpoint.url == "https://example.test/story"
    assert checkpoint.title == "Test Story"
    assert len(checkpoint.player_characters) == 1
    assert checkpoint.player_characters[0].name == "Del"
    assert len(checkpoint.locations) == 1
    assert checkpoint.locations[0].name == "Marsh"
    assert len(checkpoint.beats) == 1
    assert checkpoint.beats[0].beat == "The party departs"


def test_build_entities_from_raw_checkpoint_json_is_valid(tmp_path):
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="A druid")],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    build_entities_from_raw(raw_path, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    loaded = WorldStateCheckpoint.model_validate(payload)
    assert loaded.player_characters[0].name == "Del"


def test_build_entities_from_raw_beats_have_no_quotes_field(tmp_path):
    raw = _make_raw(
        outline=["### The ambush begins", "### The party flees"],
        quotes=[
            ScrapedQuote(text="Keep moving.", attribution="Vendetta"),
            ScrapedQuote(text="Watch out!", attribution="Del"),
        ],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert len(checkpoint.beats) == 2
    assert not hasattr(checkpoint.beats[0], "quotes")


def test_build_entities_from_raw_later_beats_no_quotes_field(tmp_path):
    raw = _make_raw(
        outline=["### Beat one", "### Beat two"],
        quotes=[ScrapedQuote(text="Hello.", attribution="Del")],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert len(checkpoint.beats) == 2
    assert not hasattr(checkpoint.beats[1], "quotes")


def test_build_entities_from_raw_skips_blank_quote_text(tmp_path):
    raw = _make_raw(
        outline=["### Beat one"],
        quotes=[
            ScrapedQuote(text="   ", attribution="Del"),
            ScrapedQuote(text="Valid quote.", attribution="Orion"),
        ],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert len(checkpoint.beats) == 1


def test_build_entities_from_raw_quotes_are_ignored(tmp_path):
    raw = _make_raw(
        outline=["### Beat one"],
        quotes=[ScrapedQuote(text="A voice cries out.", attribution=None)],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert not hasattr(checkpoint.beats[0], "quotes")


def test_build_entities_from_raw_no_quotes_leaves_empty_beat_quotes(tmp_path):
    raw = _make_raw(outline=["### Beat one"], quotes=[])
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert len(checkpoint.beats) == 1



def test_write_entities_bible_prefers_existing_bible_before_previous_versions(monkeypatch, tmp_path):
    campaign_root = tmp_path / "campaigns" / "flail"
    episode_dir = campaign_root / "flail-the-curse-of-the-dreadmarsh-witch-pt-1"
    version_dir = episode_dir / "v002"
    previous_dir = episode_dir / "v001"
    version_dir.mkdir(parents=True)
    previous_dir.mkdir(parents=True)

    existing_bible = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Dreadmarsh Crossing",
        author="GM",
        model="bible",
        player_characters=[
            Character(name="Wulf", description="Bible description.", class_name="Ranger", race="Human")
        ],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    (campaign_root / "entities_bible.json").write_text(
        existing_bible.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (previous_dir / "02_entities.json").write_text(
        WorldStateCheckpoint(
            url="https://example.test/story-previous",
            title="Previous Episode",
            author="GM",
            model="scraper-direct",
            player_characters=[Character(name="Wulf", description="Previous version.")],
            npcs=[],
            locations=[],
            beats=[],
            analyzed_at="2026-05-04T00:00:00+00:00",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    incoming = WorldStateCheckpoint(
        url="https://example.test/story-current",
        title="Current Episode",
        author="GM",
        model="scraper-direct",
        player_characters=[Character(name="Wulf", description="Current version.")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    entities_path = version_dir / "02_entities.json"
    entities_path.write_text(incoming.model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr("entities._merge_entities_with_llm", _fake_continuity_merge)

    _, _, merged, _ = write_entities_bible(
        campaign_root=campaign_root,
        version_dir=version_dir,
        entities_path=entities_path,
    )

    assert merged.player_characters[0].class_name == "Ranger"


def test_write_entities_bible_uses_previous_version_entities_when_bible_missing(monkeypatch, tmp_path):
    campaign_root = tmp_path / "campaigns" / "flail"
    episode_dir = campaign_root / "flail-the-curse-of-the-dreadmarsh-witch-pt-1"
    version_dir = episode_dir / "v002"
    previous_dir = episode_dir / "v001"
    version_dir.mkdir(parents=True)
    previous_dir.mkdir(parents=True)

    previous_entities = WorldStateCheckpoint(
        url="https://example.test/story-previous",
        title="Previous Episode",
        author="GM",
        model="scraper-direct",
        player_characters=[Character(name="Wulf", description="Previous version.", class_name="Ranger")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    (previous_dir / "02_entities.json").write_text(
        previous_entities.model_dump_json(indent=2),
        encoding="utf-8",
    )

    incoming = WorldStateCheckpoint(
        url="https://example.test/story-current",
        title="Current Episode",
        author="GM",
        model="scraper-direct",
        player_characters=[Character(name="Wulf", description="Current version.")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    entities_path = version_dir / "02_entities.json"
    entities_path.write_text(incoming.model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr("entities._merge_entities_with_llm", _fake_continuity_merge)

    _, _, merged, _ = write_entities_bible(
        campaign_root=campaign_root,
        version_dir=version_dir,
        entities_path=entities_path,
    )

    assert merged.player_characters[0].class_name == "Ranger"


def test_write_entities_bible_uses_latest_previous_episode_when_no_local_history(monkeypatch, tmp_path):
    campaign_root = tmp_path / "campaigns" / "flail"
    current_episode = campaign_root / "flail-the-curse-of-the-dreadmarsh-witch-pt-2"
    previous_episode = campaign_root / "flail-the-curse-of-the-dreadmarsh-witch-pt-1"
    current_version = current_episode / "v001"
    previous_version = previous_episode / "v002"
    current_version.mkdir(parents=True)
    previous_version.mkdir(parents=True)

    (current_episode / "episode_meta.json").write_text(
        json.dumps({"slug": current_episode.name, "title": "Current", "created_at": "2026-06-02T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (previous_episode / "episode_meta.json").write_text(
        json.dumps({"slug": previous_episode.name, "title": "Previous", "created_at": "2026-06-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (previous_version / "02_entities.json").write_text(
        WorldStateCheckpoint(
            url="https://example.test/story-previous",
            title="Previous Episode",
            author="GM",
            model="scraper-direct",
            player_characters=[Character(name="Wulf", description="Previous episode.", class_name="Ranger")],
            npcs=[],
            locations=[],
            beats=[],
            analyzed_at="2026-05-04T00:00:00+00:00",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    incoming = WorldStateCheckpoint(
        url="https://example.test/story-current",
        title="Current Episode",
        author="GM",
        model="scraper-direct",
        player_characters=[Character(name="Wulf", description="Current episode.")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    entities_path = current_version / "02_entities.json"
    entities_path.write_text(incoming.model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr("entities._merge_entities_with_llm", _fake_continuity_merge)

    _, _, merged, _ = write_entities_bible(
        campaign_root=campaign_root,
        version_dir=current_version,
        entities_path=entities_path,
    )

    assert merged.player_characters[0].class_name == "Ranger"


def test_write_entities_bible_uses_llm_continuity_merge(monkeypatch, tmp_path):
    campaign_root = tmp_path / "campaigns" / "flail"
    episode_dir = campaign_root / "flail-the-curse-of-the-dreadmarsh-witch-pt-1"
    version_dir = episode_dir / "v001"
    version_dir.mkdir(parents=True)

    existing = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Dreadmarsh Crossing",
        author="GM",
        model="bible",
        player_characters=[Character(name="Wulf", description="Canonical sailor.", class_name="Ranger")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    incoming = WorldStateCheckpoint(
        url="https://example.test/story-2",
        title="Another Episode",
        author="GM",
        model="scraper-direct",
        player_characters=[Character(name="Wulf", description="Episode sailor.")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    merged = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Dreadmarsh Crossing",
        author="GM",
        model="bible",
        player_characters=[Character(name="Wulf", description="LLM merged sailor.", class_name="Ranger", race="Human")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )

    (campaign_root / "entities_bible.json").write_text(
        existing.model_dump_json(indent=2),
        encoding="utf-8",
    )

    def fake_llm_merge(existing_checkpoint, incoming_checkpoint, model="test-model"):
        assert existing_checkpoint == existing
        assert incoming_checkpoint == incoming
        return merged, ["LLM warning"]

    monkeypatch.setattr("entities._merge_entities_with_llm", fake_llm_merge)

    entities_path = version_dir / "02_entities.json"
    entities_path.write_text(incoming.model_dump_json(indent=2), encoding="utf-8")

    bible_path, version_copy_path, result, warnings = write_entities_bible(
        campaign_root=campaign_root,
        version_dir=version_dir,
        entities_path=entities_path,
    )

    assert bible_path.exists()
    assert version_copy_path.exists()
    assert result == merged
    assert warnings == ["LLM warning"]
    assert json.loads(bible_path.read_text(encoding="utf-8"))["player_characters"][0]["description"] == "LLM merged sailor."


def test_write_entities_bible_creates_campaign_root_and_version_copy(monkeypatch, tmp_path):
    campaign_root = tmp_path / "campaigns" / "flail"
    episode_dir = campaign_root / "flail-the-curse-of-the-dreadmarsh-witch-pt-1"
    version_dir = episode_dir / "v001"
    version_dir.mkdir(parents=True)

    existing_bible = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Dreadmarsh Crossing",
        author="GM",
        model="bible",
        player_characters=[
            Character(
                name="Wulf",
                description="A weathered sailor.",
                class_name="Ranger",
                race="Human",
                aliases=["Wolf"],
            )
        ],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    (campaign_root / "entities_bible.json").write_text(
        existing_bible.model_dump_json(indent=2),
        encoding="utf-8",
    )

    incoming = WorldStateCheckpoint(
        url="https://example.test/story-2",
        title="Another Episode",
        author="GM",
        model="scraper-direct",
        player_characters=[
            Character(
                name="wulf",
                description="A weathered sailor with a red scarf.",
                aliases=["Wolfie"],
            )
        ],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    entities_path = version_dir / "02_entities.json"
    entities_path.write_text(incoming.model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr("entities._merge_entities_with_llm", _fake_continuity_merge)

    bible_path, version_copy_path, merged, warnings = write_entities_bible(
        campaign_root=campaign_root,
        version_dir=version_dir,
        entities_path=entities_path,
    )

    assert bible_path == campaign_root / "entities_bible.json"
    assert version_copy_path == version_dir / "02_5_entities_bible.json"
    assert version_copy_path.exists()
    assert merged.player_characters[0].name == "Wulf"
    assert merged.player_characters[0].description == "A weathered sailor with a red scarf."
    assert any("description" in warning.lower() for warning in warnings)


def test_write_entities_bible_writes_locations_and_beats_from_merge_result(monkeypatch, tmp_path):
    """Covers that write_entities_bible correctly persists locations and beats (and character
    name priority) coming from the continuity merge into both the campaign bible and the
    version-local 02_5 copy. The actual merging/dedup rules live in the LLM + prompts;
    here we use a local patch returning an explicit clean result and assert on the outputs
    (files written, returned checkpoint).
    """
    campaign_root = tmp_path / "campaigns" / "belowdown"
    episode_dir = campaign_root / "belowdown-ep-12"
    version_dir = episode_dir / "v016"
    version_dir.mkdir(parents=True)

    existing = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Belowdown Ep. 12",
        author=None,
        model="bible",
        player_characters=[Character(name="Wulf", description="Orc tank.", class_name="Sea Wolf", race="Orc", aliases=[])],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    (campaign_root / "entities_bible.json").write_text(existing.model_dump_json(indent=2), encoding="utf-8")

    incoming = WorldStateCheckpoint(
        url="https://example.test/story-current",
        title="Belowdown Ep. 12",
        author=None,
        model="scraper-direct",
        player_characters=[Character(name="Wolf", description="Orc warrior.", class_name="Sea Wolf", race="Orc", aliases=[])],
        npcs=[],
        locations=[
            Location(name="Dungeon - 13th Floor", appearance="Dusty dangerous level."),
            Location(name="The Town", appearance="Main hub."),
        ],
        beats=[
            StoryBeat(index=1, beat="Descent and Ambush", highlights=["Skeletons."]),
            StoryBeat(index=2, beat="Gargoyles", highlights=["Defeated."]),
        ],
        analyzed_at="2026-06-14T00:00:00+00:00",
    )
    entities_path = version_dir / "02_entities.json"
    entities_path.write_text(incoming.model_dump_json(indent=2), encoding="utf-8")

    # Explicit clean result that "the LLM" (per prompt rules) would return:
    # bible name kept, incoming name in aliases, locs/beats as provided by merge (no dups).
    clean_merged = WorldStateCheckpoint(
        url=existing.url,
        title=existing.title,
        author=existing.author,
        model="bible",
        player_characters=[
            Character(
                name="Wulf",
                description="Orc warrior.",
                class_name="Sea Wolf",
                race="Orc",
                aliases=["Wolf"],
            )
        ],
        npcs=[],
        locations=[
            Location(name="Dungeon - 13th Floor", appearance="Dusty dangerous level."),
            Location(name="The Town", appearance="Main hub."),
        ],
        beats=[
            StoryBeat(index=1, beat="Descent and Ambush", highlights=["Skeletons."]),
            StoryBeat(index=2, beat="Gargoyles", highlights=["Defeated."]),
        ],
        analyzed_at=existing.analyzed_at,
    )

    def local_fake_merge(ex, inc, model="test-model"):
        return clean_merged, ["name priority applied"]

    monkeypatch.setattr("entities._merge_entities_with_llm", local_fake_merge)

    bible_path, version_copy_path, result, warnings = write_entities_bible(
        campaign_root=campaign_root,
        version_dir=version_dir,
        entities_path=entities_path,
    )

    assert bible_path == campaign_root / "entities_bible.json"
    assert version_copy_path == version_dir / "02_5_entities_bible.json"
    assert bible_path.exists() and version_copy_path.exists()
    assert result == clean_merged
    assert any("name priority" in w.lower() for w in warnings)

    written = json.loads(bible_path.read_text(encoding="utf-8"))
    assert written["player_characters"][0]["name"] == "Wulf"
    assert "Wolf" in written["player_characters"][0]["aliases"]
    assert len(written["locations"]) == 2
    assert len(written["beats"]) == 2


def test_project_episode_entities_keeps_only_episode_cast_with_bible_records():
    episode = WorldStateCheckpoint(
        url="https://example.test/ep3",
        title="Bells Pt 3",
        author="GM",
        model="scraper-direct",
        player_characters=[
            Character(name="Maisie Faye", description="Episode-only seer blurb."),
            Character(name="Vincent Poe", description="Episode Vincent."),
        ],
        npcs=[
            Character(name="Choir Master", description="Episode choir master."),
            Character(name="One-off Guard", description="Only appears this episode."),
        ],
        locations=[
            Location(name="Cathedral", appearance="Episode damp cathedral."),
            Location(name="New Pier", appearance="Only this episode."),
        ],
        beats=[
            StoryBeat(index=1, beat="Episode beat", highlights=["Episode beat"]),
        ],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    bible = WorldStateCheckpoint(
        url="https://example.test/campaign",
        title="Campaign bible",
        author="GM",
        model="bible",
        player_characters=[
            Character(
                name="Amos",
                description="Campaign-only party member from earlier episodes.",
                physical_description="Average build.",
            ),
            Character(
                name="Maisie Fae",
                description="Canonical seer.",
                aliases=["Maisie Faye"],
                physical_description="Sharp-eyed seer.",
                character_quirks="Snarky.",
            ),
            Character(
                name="Vincent Poe",
                description="Canonical Vincent.",
                physical_description="All black.",
            ),
        ],
        npcs=[
            Character(
                name="Choir Master",
                description="Canonical choir master.",
                character_quirks="Cowardly.",
            ),
            Character(name="Black Mary", description="Not in this episode."),
        ],
        locations=[
            Location(name="Cathedral", appearance="Canonical rot-filled cathedral."),
            Location(name="Old Sewers", appearance="Not in this episode."),
        ],
        beats=[
            StoryBeat(index=1, beat="Old campaign beat", highlights=["old"]),
        ],
        analyzed_at="2026-01-01T00:00:00+00:00",
    )

    projected = project_episode_entities(episode, bible)

    assert [c.name for c in projected.player_characters] == ["Maisie Fae", "Vincent Poe"]
    assert projected.player_characters[0].physical_description == "Sharp-eyed seer."
    assert projected.player_characters[0].aliases == ["Maisie Faye"]
    assert projected.player_characters[1].physical_description == "All black."

    assert [c.name for c in projected.npcs] == ["Choir Master", "One-off Guard"]
    assert projected.npcs[0].character_quirks == "Cowardly."
    assert projected.npcs[1].description == "Only appears this episode."

    # Full-campaign cast must not leak in.
    assert all(c.name != "Amos" for c in projected.player_characters)
    assert all(c.name != "Black Mary" for c in projected.npcs)

    assert [loc.name for loc in projected.locations] == ["Cathedral", "New Pier"]
    assert projected.locations[0].appearance == "Canonical rot-filled cathedral."
    assert projected.locations[1].appearance == "Only this episode."

    # Beats stay episode-local.
    assert projected.beats == episode.beats
    assert projected.url == episode.url
    assert projected.title == episode.title


def test_write_episode_entities_writes_checkpoint(tmp_path):
    episode = WorldStateCheckpoint(
        url="https://example.test/ep",
        title="Ep",
        author=None,
        model="scraper-direct",
        player_characters=[Character(name="Wolf", description="Episode spelling.")],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    bible = WorldStateCheckpoint(
        url="https://example.test/campaign",
        title="Campaign",
        author=None,
        model="bible",
        player_characters=[
            Character(name="Wulf", description="Canonical.", aliases=["Wolf"]),
        ],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-01-01T00:00:00+00:00",
    )
    episode_path = tmp_path / "02_entities.json"
    output_path = tmp_path / EPISODE_ENTITIES_FILENAME
    episode_path.write_text(episode.model_dump_json(indent=2), encoding="utf-8")

    result = write_episode_entities(
        entities_path=episode_path,
        bible=bible,
        output_path=output_path,
    )

    assert output_path.exists()
    assert result.player_characters[0].name == "Wulf"
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["player_characters"][0]["name"] == "Wulf"
    assert written["player_characters"][0]["aliases"] == ["Wolf"]


def test_merge_entities_with_llm_uses_payload_for_locations_and_beats(monkeypatch):
    """Directly exercises the fixed _merge_entities_with_llm: it must take locations and
    beats from the LLM payload (the source of truth per the continuity prompt) rather
    than performing its own concatenation. Mocks only the external LLM API boundary.
    """
    from unittest.mock import MagicMock

    existing = WorldStateCheckpoint(
        url="u",
        title="t",
        author=None,
        model="m",
        player_characters=[],
        npcs=[],
        locations=[Location(name="Old Place", appearance="old")],
        beats=[StoryBeat(index=1, beat="Old Beat", highlights=["old"])],
        analyzed_at="2026-01-01T00:00:00+00:00",
    )
    incoming = WorldStateCheckpoint(
        url="u2",
        title="t2",
        author=None,
        model="m",
        player_characters=[],
        npcs=[],
        locations=[Location(name="New Place", appearance="new")],
        beats=[StoryBeat(index=1, beat="New Beat", highlights=["new"])],
        analyzed_at="2026-01-02T00:00:00+00:00",
    )

    # The "LLM" returns a payload that has already done the (dedup/priority) work.
    payload_json = json.dumps({
        "player_characters": [],
        "npcs": [],
        "locations": [{"name": "Canonical Place", "appearance": "from bible or merged"}],
        "beats": [{"index": 1, "beat": "Canonical Beat", "highlights": ["from llm"]}],
        "warnings": ["llm did the merge"],
    })

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=payload_json))]
    )

    monkeypatch.setattr("entities.build_openai_client", lambda model: mock_client)

    merged, warnings = entities._merge_entities_with_llm(existing, incoming)

    # Must come from payload, not any concat of existing + incoming
    assert len(merged.locations) == 1
    assert merged.locations[0].name == "Canonical Place"
    assert merged.locations[0].appearance == "from bible or merged"

    assert len(merged.beats) == 1
    assert merged.beats[0].beat == "Canonical Beat"

    assert "llm did the merge" in warnings


# ---------------------------------------------------------------------------
# Character prompt formatting
# ---------------------------------------------------------------------------


def test_format_character_details_puts_race_and_class_beside_name():
    character = Character(
        name="Del",
        description="A druid in mossy robes",
        **{"class": "Druid"},
        race="Half-Elf",
        physical_description="Tall and moss-slick.",
        clothing_armor="Mossy robes.",
    )

    text = entities.format_character_details(character)

    assert text.startswith("Del (Race: Half-Elf; Class: Druid):")
    assert "Physical: Tall and moss-slick." in text
    assert "Clothing/Armor: Mossy robes." in text
    assert "Physical:" in text.splitlines()[1]


def test_format_character_details_uses_line_breaks_and_labeled_fields():
    character = Character(
        name="Del",
        description="fallback",
        physical_description="Tall.",
        clothing_armor="Robes.",
        weapons="Staff.",
        character_quirks="Sniffs the air.",
    )

    text = entities.format_character_details(character)
    lines = text.splitlines()

    assert lines[0] == "Del:"
    assert lines[1] == "Physical: Tall."
    assert lines[2] == "Clothing/Armor: Robes."
    assert lines[3] == "Weapons: Staff."
    assert lines[4] == "Quirks: Sniffs the air."


def test_format_character_details_skips_none_and_unknown_attributes():
    character = Character(
        name="Del",
        description="A druid in mossy robes",
        **{"class": "Unknown"},
        race="None.",
        physical_description="None",
        clothing_armor="  unknown  ",
        weapons="Staff.",
        character_quirks="None.",
    )

    text = entities.format_character_details(character)

    assert "Race:" not in text
    assert "Class:" not in text
    assert "Physical:" not in text
    assert "Clothing/Armor:" not in text
    assert "Quirks:" not in text
    assert "Weapons: Staff." in text
    assert text.startswith("Del:")


def test_format_character_details_falls_back_to_description():
    character = Character(name="Del", description="A druid in mossy robes")

    text = entities.format_character_details(character)

    assert text == "Del:\nA druid in mossy robes"


def test_format_character_details_bullet_mode_indents_body():
    character = Character(
        name="Del",
        description="fallback",
        race="Elf",
        physical_description="Tall.",
        weapons="Bow.",
    )

    text = entities.format_character_details(character, bullet=True)
    lines = text.splitlines()

    assert lines[0] == "- Del (Race: Elf):"
    assert lines[1] == "  Physical: Tall."
    assert lines[2] == "  Weapons: Bow."

