import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from entities import (
    Character,
    Location,
    Quote,
    StoryBeat,
    WorldStateCheckpoint,
    _build_beats,
    _build_characters,
    _build_locations,
    _dedupe_by_name,
    build_entities_from_raw,
)
from scraper import RawTextCheckpoint, ScrapedEntity, ScrapedQuote


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# _build_characters
# ---------------------------------------------------------------------------


def test_build_characters_merges_player_characters_and_npcs():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="druid")],
        npcs=[ScrapedEntity(name="Merelda", description="witch")],
    )
    chars = _build_characters(raw)
    assert [c.name for c in chars] == ["Del", "Merelda"]


def test_build_characters_player_characters_listed_first():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Orion", description="bard")],
        npcs=[ScrapedEntity(name="Merelda", description="witch")],
    )
    chars = _build_characters(raw)
    assert chars[0].name == "Orion"
    assert chars[1].name == "Merelda"


def test_build_characters_deduplicates_across_lists():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="PC description")],
        npcs=[ScrapedEntity(name="del", description="NPC description")],
    )
    chars = _build_characters(raw)
    assert len(chars) == 1
    assert chars[0].name == "Del"
    assert chars[0].description == "PC description"


def test_build_characters_fallback_description_when_none():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description=None)],
    )
    chars = _build_characters(raw)
    assert chars[0].description == "No source description provided."


def test_build_characters_fallback_description_when_whitespace_only():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="   ")],
    )
    chars = _build_characters(raw)
    assert chars[0].description == "No source description provided."


def test_build_characters_demeanor_always_unknown():
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="A druid")]
    )
    chars = _build_characters(raw)
    assert chars[0].demeanor == "Unknown"


def test_build_characters_empty_when_no_entities():
    raw = _make_raw()
    assert _build_characters(raw) == []


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
    raw = _make_raw(outline=["Beat one", "Beat two", "Beat three"])
    beats = _build_beats(raw)
    assert [b.index for b in beats] == [1, 2, 3]
    assert [b.text for b in beats] == ["Beat one", "Beat two", "Beat three"]


def test_build_beats_skips_blank_outline_entries():
    raw = _make_raw(outline=["Beat one", "  ", "Beat three"])
    beats = _build_beats(raw)
    assert len(beats) == 2
    assert beats[0].text == "Beat one"
    assert beats[1].text == "Beat three"


def test_build_beats_falls_back_to_content_when_outline_empty():
    raw = _make_raw(content="Del crossed the marsh.", outline=[])
    beats = _build_beats(raw)
    assert len(beats) == 1
    assert beats[0].index == 1
    assert beats[0].text == "Del crossed the marsh."


def test_build_beats_normalizes_content_whitespace():
    raw = _make_raw(content="Del  crossed\n\nthe   marsh.", outline=[])
    beats = _build_beats(raw)
    assert beats[0].text == "Del crossed the marsh."


def test_build_beats_quotes_empty_by_default():
    raw = _make_raw(outline=["Beat one"])
    beats = _build_beats(raw)
    assert beats[0].quotes == []


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
        outline=["The party departs"],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert output_path.exists()
    assert checkpoint.model == "scraper-direct"
    assert checkpoint.url == "https://example.test/story"
    assert checkpoint.title == "Test Story"
    assert len(checkpoint.characters) == 1
    assert checkpoint.characters[0].name == "Del"
    assert len(checkpoint.locations) == 1
    assert checkpoint.locations[0].name == "Marsh"
    assert len(checkpoint.beats) == 1
    assert checkpoint.beats[0].text == "The party departs"


def test_build_entities_from_raw_checkpoint_json_is_valid(tmp_path):
    raw = _make_raw(
        player_characters=[ScrapedEntity(name="Del", description="A druid")],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    build_entities_from_raw(raw_path, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    loaded = WorldStateCheckpoint.model_validate(payload)
    assert loaded.characters[0].name == "Del"


def test_build_entities_from_raw_attaches_quotes_to_first_beat(tmp_path):
    raw = _make_raw(
        outline=["The ambush begins", "The party flees"],
        quotes=[
            ScrapedQuote(text="Keep moving.", attribution="Vendetta"),
            ScrapedQuote(text="Watch out!", attribution="Del"),
        ],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    first_beat = checkpoint.beats[0]
    assert len(first_beat.quotes) == 2
    assert first_beat.quotes[0].speaker == "Vendetta"
    assert first_beat.quotes[0].text == "Keep moving."
    assert first_beat.quotes[1].speaker == "Del"


def test_build_entities_from_raw_later_beats_have_no_quotes(tmp_path):
    raw = _make_raw(
        outline=["Beat one", "Beat two"],
        quotes=[ScrapedQuote(text="Hello.", attribution="Del")],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert checkpoint.beats[1].quotes == []


def test_build_entities_from_raw_skips_blank_quote_text(tmp_path):
    raw = _make_raw(
        outline=["Beat one"],
        quotes=[
            ScrapedQuote(text="   ", attribution="Del"),
            ScrapedQuote(text="Valid quote.", attribution="Orion"),
        ],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert len(checkpoint.beats[0].quotes) == 1
    assert checkpoint.beats[0].quotes[0].speaker == "Orion"


def test_build_entities_from_raw_quote_attribution_fallback(tmp_path):
    raw = _make_raw(
        outline=["Beat one"],
        quotes=[ScrapedQuote(text="A voice cries out.", attribution=None)],
    )
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert checkpoint.beats[0].quotes[0].speaker == "Unknown"


def test_build_entities_from_raw_no_quotes_leaves_empty_beat_quotes(tmp_path):
    raw = _make_raw(outline=["Beat one"], quotes=[])
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path)

    assert checkpoint.beats[0].quotes == []


def test_build_entities_from_raw_custom_model_label(tmp_path):
    raw = _make_raw()
    raw_path = _write_raw(tmp_path, raw)
    output_path = tmp_path / "02_entities.json"

    checkpoint = build_entities_from_raw(raw_path, output_path, model_label="test-label")

    assert checkpoint.model == "test-label"
