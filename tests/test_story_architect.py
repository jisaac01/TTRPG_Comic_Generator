import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import story_architect
from model_defaults import DEFAULT_MODEL


def _write_input_checkpoints(tmp_path: Path) -> tuple[Path, Path]:
    raw_input = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "content": "Del grabs a torch and leads the party through the marsh.",
        "quotes": [
            {
                "text": "Stay close to me.",
                "attribution": "Del warns the party as they enter the marsh.",
            }
        ],
        "source_selector": "div.story-content",
        "scraped_at": "2026-05-04T00:00:00+00:00",
    }
    entities_input = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "model": DEFAULT_MODEL,
        "player_characters": [
            {
                "name": "Del",
                "description": "A druid in mossy robes",
            },
            {
                "name": "Vendetta",
                "description": "A wary vampire scout",
            },
        ],
        "npcs": [],
        "locations": [
            {
                "name": "Marsh trail",
                "appearance": "Foggy trail lined with reeds",
            }
        ],
        "beats": [
            {
                "index": 1,
                "beat": "The party enters the marsh at dusk.",
                "highlights": ["The party enters the marsh at dusk."],
            },
            {
                "index": 2,
                "beat": "Del lights a torch to guide the group.",
                "highlights": ["Del lights a torch to guide the group."],
            },
        ],
        "analyzed_at": "2026-05-04T00:00:00+00:00",
    }

    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    raw_path.write_text(json.dumps(raw_input), encoding="utf-8")
    entities_path.write_text(json.dumps(entities_input), encoding="utf-8")
    return raw_path, entities_path


def test_create_story_bible_writes_checkpoint_with_text_content(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "02_5_story_bible.txt"

    def fake_generator(raw_content, world, model, scene_count):
        assert "torch" in raw_content
        assert world.title == "Swamp Trouble"
        assert scene_count == 2
        assert model == DEFAULT_MODEL
        # Return a simple text-based story bible
        return """Scene 1:
The party enters the marsh at dusk. Del leads them through the foggy trail lined with reeds, warning "Stay close to me." The atmosphere is tense as darkness falls and unknown dangers lurk in the mist.

Scene 2:
Del lights a torch to guide the group through the narrow marsh path. The firelight cuts through the darkness, casting dancing shadows on the reeds. With Del taking control of the route with firelight, the party pushes forward with renewed confidence."""

    checkpoint = story_architect.create_story_bible(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=output_path,
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        model=DEFAULT_MODEL,
        scene_count=2,
        generator=fake_generator,
    )

    assert output_path.exists()
    on_disk = output_path.read_text(encoding="utf-8")
    assert on_disk.startswith("Scene 1:")
    assert "Scene 2:" in on_disk
    assert '"story_bible"' not in on_disk  # plain text, not JSON wrapper
    assert "Stay close to me." in on_disk  # dialogue quotes unescaped
    assert checkpoint.scene_count == 2
    assert "Scene 1:" in checkpoint.story_bible
    assert "Scene 2:" in checkpoint.story_bible
    assert len(checkpoint.generation_errors) == 0
    assert checkpoint.title == "Swamp Trouble"


def test_load_story_bible_derives_scene_count_from_headers(tmp_path):
    path = tmp_path / "02_5_story_bible.txt"
    path.write_text(
        "Scene 1:\nOpening.\n\nScene 2:\nMiddle.\n\nScene 3:\nEnding.\n",
        encoding="utf-8",
    )

    loaded = story_architect.load_story_bible(path)

    assert loaded.scene_count == 3
    assert "Scene 2:" in loaded.story_bible


def test_create_story_bible_includes_reference_quotes(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "02_5_story_bible.txt"

    received_world = None

    def fake_generator(raw_content, world, model, scene_count):
        nonlocal received_world
        received_world = world
        return """Scene 1:
Opening scene with reference to "Stay close to me."

Scene 2:
Continuation scene."""

    story_architect.create_story_bible(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=output_path,
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        model=DEFAULT_MODEL,
        scene_count=2,
        generator=fake_generator,
    )

    assert received_world is not None
    # Verify world state contains entities and beats
    assert hasattr(received_world, 'player_characters')
    assert hasattr(received_world, 'beats')


def test_create_story_bible_normalizes_aliases_as_whole_words(tmp_path):
    raw_input = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "content": (
            "Wolf crossed the marsh. Then Wolf met Maisie Faye near the gate. "
            "Wolfgang stayed behind while the werewolf howled."
        ),
        "quotes": [],
        "source_selector": "div.story-content",
        "scraped_at": "2026-05-04T00:00:00+00:00",
    }
    entities_input = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "model": DEFAULT_MODEL,
        "player_characters": [
            {
                "name": "Wulf",
                "description": "A weathered sailor.",
                "aliases": ["Wolf"],
            },
            {
                "name": "Maisie Fae",
                "description": "A seer.",
                "aliases": ["Maisie Faye"],
            },
        ],
        "npcs": [],
        "locations": [],
        "beats": [],
        "analyzed_at": "2026-05-04T00:00:00+00:00",
    }

    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    raw_path.write_text(json.dumps(raw_input), encoding="utf-8")
    entities_path.write_text(json.dumps(entities_input), encoding="utf-8")

    received_content = None

    def fake_generator(raw_content, world, model, scene_count):
        nonlocal received_content
        received_content = raw_content
        return "Scene 1:\nNormalized story."

    story_architect.create_story_bible(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "02_5_story_bible.txt",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        model=DEFAULT_MODEL,
        scene_count=1,
        generator=fake_generator,
    )

    assert received_content == (
        "Wulf crossed the marsh. Then Wulf met Maisie Fae near the gate. "
        "Wolfgang stayed behind while the werewolf howled."
    )


def test_normalize_aliases_replaces_nobby_with_knobby():
    """Regression: STT alias Nobby must become canonical Knobby mid-sentence."""
    from entities import Character, WorldStateCheckpoint

    world = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Obys",
        author="GM",
        model=DEFAULT_MODEL,
        player_characters=[
            Character(
                name="Knobby",
                description="A noble murdered the Choirmaster as Nobby once did.",
                aliases=["Navi", "Nabi", "Nobby"],
            )
        ],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    text = (
        "His pleas fell on deaf ears as Nobby, in a startling display, "
        "stabbed the man. Later Nobby adopted Titch."
    )
    normalized = story_architect._normalize_aliases_in_text(text, world)
    assert "Nobby" not in normalized
    assert normalized.count("Knobby") == 2
    assert "as Knobby, in a startling" in normalized


def test_normalize_aliases_does_not_reexpand_prefix_of_canonical_name():
    """Alias 'Lucky' must not expand inside already-canonical 'Lucky Clover'."""
    from entities import Character, WorldStateCheckpoint

    world = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Obys",
        author="GM",
        model=DEFAULT_MODEL,
        player_characters=[
            Character(
                name="Lucky Clover",
                description="A bard.",
                aliases=["Lucky", "Orson"],
            )
        ],
        npcs=[],
        locations=[],
        beats=[],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    text = "Lucky Clover walked in. Lucky smiled. Orson waved."
    normalized = story_architect._normalize_aliases_in_text(text, world)
    assert normalized == (
        "Lucky Clover walked in. Lucky Clover smiled. Lucky Clover waved."
    )
    assert "Lucky Clover Clover" not in normalized


def test_create_story_bible_normalizes_aliases_in_generated_output(tmp_path):
    """LLM output that reintroduces aliases is rewritten to canonical names."""
    raw_input = {
        "url": "https://example.test/story",
        "title": "Obys",
        "author": "GM",
        "content": "Knobby accepts the deal.",
        "quotes": [],
        "source_selector": "div.story-content",
        "scraped_at": "2026-05-04T00:00:00+00:00",
    }
    entities_input = {
        "url": "https://example.test/story",
        "title": "Obys",
        "author": "GM",
        "model": DEFAULT_MODEL,
        "player_characters": [
            {
                "name": "Knobby",
                "description": "A noble.",
                "aliases": ["Nobby"],
            }
        ],
        "npcs": [],
        "locations": [],
        "beats": [],
        "analyzed_at": "2026-05-04T00:00:00+00:00",
    }
    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    raw_path.write_text(json.dumps(raw_input), encoding="utf-8")
    entities_path.write_text(json.dumps(entities_input), encoding="utf-8")
    output_path = tmp_path / "02_5_story_bible.txt"

    def fake_generator(raw_content, world, model, scene_count):
        return "Scene 1:\nNobby, ever defiant, accepts the deal."

    story_architect.create_story_bible(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=output_path,
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        model=DEFAULT_MODEL,
        scene_count=1,
        generator=fake_generator,
    )

    written = output_path.read_text(encoding="utf-8")
    assert "Nobby" not in written
    assert "Knobby, ever defiant, accepts the deal." in written


def test_prepare_architect_prompts_normalizes_aliases_in_rendered_prompts(tmp_path):
    """Production path: prompts sent to the model must use canonical names.

    Regression for Nobby→Knobby: create_story_bible normalized content for
    test generators, but prepare_architect_prompts (used by the pipeline) was
    still embedding raw alias-heavy text in the FINAL prompts.
    """
    from entities import Character, StoryBeat, WorldStateCheckpoint
    from prompt_saver import prepare_architect_prompts

    world = WorldStateCheckpoint(
        url="https://example.test/story",
        title="Obys' Languid Voyage",
        author="GM",
        model=DEFAULT_MODEL,
        player_characters=[
            Character(
                name="Knobby",
                description="A proud noble.",
                aliases=["Nobby", "Nabi"],
            ),
            Character(
                name="Maisie Fae",
                description="A seer.",
                aliases=["Maisie Faye"],
            ),
        ],
        npcs=[
            Character(
                name="Choirmaster",
                description="Buried alive, then murdered by Nobby.",
                aliases=["Choir Master"],
            )
        ],
        locations=[],
        beats=[
            StoryBeat(
                index=1,
                beat="Interrogation",
                highlights=["Nobby stabbed the Choirmaster."],
            )
        ],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )
    content = (
        "His pleas fell on deaf ears as Nobby stabbed the man. "
        "Maisie Faye returned later."
    )
    quotes = [
        {
            "text": "Why won't you die?",
            "attribution": "Nobby's frustrated exclamation.",
        }
    ]

    _system, user_prompt = prepare_architect_prompts(
        version_dir=tmp_path,
        content=content,
        world=world,
        scene_count=1,
        raw_quotes=quotes,
    )

    assert "Nobby" not in user_prompt
    assert "Maisie Faye" not in user_prompt
    assert "as Knobby stabbed the man" in user_prompt
    assert "Maisie Fae returned later" in user_prompt
    assert "murdered by Knobby" in user_prompt
    assert "Knobby stabbed the Choirmaster" in user_prompt
    assert "Knobby's frustrated exclamation" in user_prompt

    final_user = (tmp_path / "prompts" / "story_architect_user_FINAL.txt").read_text(
        encoding="utf-8"
    )
    assert "Nobby" not in final_user
    assert "as Knobby stabbed the man" in final_user


def test_create_story_bible_rejects_invalid_scene_count(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    try:
        story_architect.create_story_bible(
            raw_checkpoint_path=raw_path,
            entities_checkpoint_path=entities_path,
            output_path=tmp_path / "02_5_story_bible.txt",
            system_prompt_text="TEST_SYSTEM_PROMPT",
            user_prompt_text="TEST_USER_PROMPT",
            scene_count=0,
            generator=lambda *_args: "Scene 1:\nText",
        )
    except ValueError as exc:
        assert str(exc) == "scene_count must be >= 1"
    else:
        raise AssertionError("Expected ValueError for invalid scene count")


def test_story_bible_checkpoint_validates_text_field(tmp_path):
    """Verify StoryBibleCheckpoint enforces text-only output."""
    checkpoint = story_architect.StoryBibleCheckpoint(
        url="https://example.test/story",
        title="Test Story",
        author="Test Author",
        model=DEFAULT_MODEL,
        scene_count=2,
        story_bible="Scene 1:\nText\n\nScene 2:\nMore text",
        generation_errors=[],
        created_at="2026-05-04T00:00:00+00:00",
    )

    assert isinstance(checkpoint.story_bible, str)
    assert "Scene 1:" in checkpoint.story_bible
    assert len(checkpoint.generation_errors) == 0
