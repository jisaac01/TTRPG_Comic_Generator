import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import scriptwriter
import master_beater
from model_defaults import DEFAULT_MODEL


def _write_input_checkpoints(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw_input = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "content": "Del grabs a torch and leads the party through the marsh.",
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
            }
        ],
        "analyzed_at": "2026-05-04T00:00:00+00:00",
    }

    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    story_bible_path = tmp_path / "02_5_story_bible.json"
    
    raw_path.write_text(json.dumps(raw_input), encoding="utf-8")
    entities_path.write_text(json.dumps(entities_input), encoding="utf-8")
    
    story_bible_checkpoint = master_beater.StoryBibleCheckpoint(
        url="https://example.test/story",
        title="Swamp Trouble",
        author="GM",
        model=DEFAULT_MODEL,
        scene_count=3,
        story_bible="""Scene 1:
The party enters the marsh at dusk. Del grabs a torch and leads the group into the murky waters. The reeds tower overhead, their silhouettes ghostly in the fading light. Del warns urgently, "Stay close to me." Nobody holds the torch yet except Del.

Scene 2:
The group pushes deeper into the marsh. The narrow path winds between walls of reeds that seem to press closer with each step. Del holds the torch aloft, casting dancing shadows. Urgency fills the air as they move forward, Del keeping the torch lit against the growing darkness.

Scene 3:
They pause at a collapsed ruin gate to reassess. The ancient structure looms before them, half-buried in the marsh. Del calls out, "We hold here." as the party takes shelter behind the crumbling stones. Del still holds the torch, the flame casting eerie shadows on the ruins.""",
        generation_errors=[],
        created_at="2026-05-04T00:00:00+00:00",
    )
    story_bible_path.write_text(story_bible_checkpoint.model_dump_json(), encoding="utf-8")
    return raw_path, entities_path, story_bible_path


def _valid_payload() -> scriptwriter.ScriptPayload:
    return scriptwriter.ScriptPayload(
        panels=[
            scriptwriter.Panel(
                index=99,
                panel_scale="large",
                panel_shape="wide",
                setting="Marsh edge",
                visual_action="Del lights a torch while Vendetta watches the reeds.",
                dialogue_overlay=["Del: Stay close."],
                held_items_before={"Del": [], "Vendetta": []},
                held_items_after={"Del": ["torch"], "Vendetta": []},
                narrative_overlays_and_text_direction=[
                    "CHYRON: Marsh Edge, Dusk",
                    "SFX: WHOOSH",
                ],
            ),
            scriptwriter.Panel(
                index=17,
                panel_scale="medium",
                panel_shape="standard",
                setting="Narrow marsh path",
                visual_action="Del leads with the torch and Vendetta tracks footprints.",
                dialogue_overlay=["Vendetta: Fresh tracks."],
                held_items_before={"Del": ["torch"], "Vendetta": []},
                held_items_after={"Del": ["torch"], "Vendetta": ["map"]},
                narrative_overlays_and_text_direction=[
                    "CAPTION: An hour later, the marsh deepens.",
                    "V.O.: Del (V.O.): I felt something watching us.",
                ],
            ),
            scriptwriter.Panel(
                index=3,
                panel_scale="small",
                panel_shape="inset",
                setting="Collapsed ruin gate",
                visual_action="Vendetta studies the map as Del keeps the torch raised.",
                dialogue_overlay=["Del: We hold here."],
                held_items_before={"Del": ["torch"], "Vendetta": ["map"]},
                held_items_after={"Del": ["torch"], "Vendetta": ["map"]},
                narrative_overlays_and_text_direction=[
                    "SFX: CRACKLE",
                    "SFX: SPLASH",
                ],
            ),
        ]
    )


def test_write_script_writes_checkpoint_and_normalizes_panel_indices(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "03_script.json"

    def fake_generator(world, story_bible, model):
        assert world.title == "Swamp Trouble"
        assert story_bible.scene_count == 3
        assert model == DEFAULT_MODEL
        return _valid_payload()

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=output_path,
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        model=DEFAULT_MODEL,
        generator=fake_generator,
    )

    assert output_path.exists()
    assert checkpoint.panel_count == 3
    assert len(checkpoint.panels) == 3
    assert [panel.index for panel in checkpoint.panels] == [1, 2, 3]
    assert checkpoint.panels[0].visual_action

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["panel_count"] == 3
    assert payload["panels"][1]["held_items_before"]["Del"] == ["torch"]


def test_write_script_accepts_any_panel_count_without_error(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        return scriptwriter.ScriptPayload(panels=_valid_payload().panels[:1])

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    assert checkpoint.panel_count == 1
    assert len(checkpoint.generation_errors) == 1
    assert "Scene count alignment failed" in checkpoint.generation_errors[0]


def test_write_script_logs_continuity_error_and_keeps_output(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        broken = _valid_payload()
        broken.panels[1].held_items_before["Del"] = []
        return broken

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    assert len(checkpoint.panels) == 3
    assert len(checkpoint.generation_errors) == 1
    assert "Continuity validation failed" in checkpoint.generation_errors[0]


def test_write_script_allows_added_items_between_panels(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        payload = _valid_payload()
        payload.panels[1].held_items_before["Del"] = ["torch", "amulet"]
        return payload

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    assert checkpoint.panels[1].held_items_before["Del"] == ["torch", "amulet"]


def test_write_script_allows_missing_character_when_inventory_empty(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        payload = _valid_payload()
        payload.panels[0].held_items_after["Vendetta"] = []
        payload.panels[1].held_items_before.pop("Vendetta", None)
        return payload

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    assert checkpoint.panel_count == 3


def test_write_script_logs_when_missing_character_with_items(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        payload = _valid_payload()
        payload.panels[0].held_items_after["Del"] = ["torch"]
        payload.panels[1].held_items_before.pop("Del", None)
        return payload

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    assert len(checkpoint.generation_errors) == 1
    assert "missing held_items_before for Del" in checkpoint.generation_errors[0]


def test_write_script_preserves_story_bible_info(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        payload = _valid_payload()
        payload.panels[0].panel_scale = "small"
        payload.panels[0].panel_shape = "irregular"
        payload.panels[1].panel_scale = "splash"
        payload.panels[1].panel_shape = "tall"
        return payload

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    assert checkpoint.panels[0].panel_scale == "small"
    assert checkpoint.panels[0].panel_shape == "irregular"
    assert checkpoint.panels[1].panel_scale == "splash"
    assert checkpoint.panels[1].panel_shape == "tall"
    assert checkpoint.panel_count == 3


def test_format_entities_for_prompt_excludes_reference_quotes():
    world = scriptwriter.WorldStateInput(
        url="https://example.test/story",
        title="Swamp Trouble",
        author="GM",
        model=DEFAULT_MODEL,
        player_characters=[],
        npcs=[],
        locations=[],
        beats=[
            scriptwriter.StoryBeat(
                index=1,
                beat="The party reaches the old bridge.",
                highlights=["The party reaches the old bridge."],
            )
        ],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )

    prompt_blob = scriptwriter._format_entities_for_prompt(world)

    assert "Story beats:" not in prompt_blob
    assert "Reference quotes:" not in prompt_blob


def test_format_story_bible_for_prompt_includes_entities(tmp_path):
    _, _, architecture_path = _write_input_checkpoints(tmp_path)

    architecture = master_beater.StoryBibleCheckpoint.model_validate_json(
        architecture_path.read_text(encoding="utf-8")
    )

    prompt_blob = scriptwriter._format_story_bible_for_prompt(architecture)

    assert "Scene 1:" in prompt_blob
    assert "Stay close to me." in prompt_blob
    assert "Scene 3:" in prompt_blob


def test_narrative_overlays_preserved_in_checkpoint(tmp_path):
    """Verify that narrative_overlays_and_text_direction is preserved."""
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        return _valid_payload()

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    assert checkpoint.panels[0].narrative_overlays_and_text_direction == [
        "CHYRON: Marsh Edge, Dusk",
        "SFX: WHOOSH",
    ]
    assert checkpoint.panels[1].narrative_overlays_and_text_direction == [
        "CAPTION: An hour later, the marsh deepens.",
        "V.O.: Del (V.O.): I felt something watching us.",
    ]
    assert checkpoint.panels[2].narrative_overlays_and_text_direction == [
        "SFX: CRACKLE",
        "SFX: SPLASH",
    ]


def test_narrative_overlays_serialized_in_json(tmp_path):
    """Verify that narrative_overlays_and_text_direction is correctly serialized to JSON."""
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "03_script.json"

    def fake_generator(_world, _architecture, _model):
        return _valid_payload()

    scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_bible_checkpoint_path=architecture_path,
        output_path=output_path,
        system_prompt_text="TEST_SYSTEM_PROMPT",
        user_prompt_text="TEST_USER_PROMPT",
        generator=fake_generator,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["panels"][0]["narrative_overlays_and_text_direction"] == [
        "CHYRON: Marsh Edge, Dusk",
        "SFX: WHOOSH",
    ]
    assert payload["panels"][1]["narrative_overlays_and_text_direction"] == [
        "CAPTION: An hour later, the marsh deepens.",
        "V.O.: Del (V.O.): I felt something watching us.",
    ]
    assert payload["panels"][2]["narrative_overlays_and_text_direction"] == [
        "SFX: CRACKLE",
        "SFX: SPLASH",
    ]
