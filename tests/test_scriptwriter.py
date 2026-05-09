import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import scriptwriter
import story_architect


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
        "model": "qwen3:8b",
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
    architecture_path = tmp_path / "02_5_story_architecture.json"
    raw_path.write_text(json.dumps(raw_input), encoding="utf-8")
    entities_path.write_text(json.dumps(entities_input), encoding="utf-8")
    architecture_path.write_text(
        json.dumps(
            {
                "url": "https://example.test/story",
                "title": "Swamp Trouble",
                "author": "GM",
                "model": "qwen3:8b",
                "target_panel_count": 3,
                "panels": [
                    {
                        "index": 1,
                        "beat_indices": [1],
                        "beat_summary": "The party enters the marsh at dusk.",
                        "story_purpose": "Establish the party entering the marsh.",
                        "panel_scale": "large",
                        "panel_shape": "wide",
                        "setting_brief": "Marsh edge at dusk",
                        "character_focus": ["Del", "Vendetta"],
                        "notable_set_dressing": ["The marsh entry"],
                        "notable_quotes": [
                            {
                                "text": "Stay close to me.",
                                "attribution_context": "Del warns the party as they enter the marsh.",
                            }
                        ],
                        "dialogue_goals": ["Caution"],
                        "continuity_notes": ["Nobody holds the torch yet"],
                    },
                    {
                        "index": 2,
                        "beat_indices": [1],
                        "beat_summary": "The group pushes deeper into the marsh.",
                        "story_purpose": "Move the party deeper into the marsh.",
                        "panel_scale": "medium",
                        "panel_shape": "standard",
                        "setting_brief": "Narrow marsh path",
                        "character_focus": ["Del", "Vendetta"],
                        "notable_set_dressing": ["Del carrying the torch"],
                        "notable_quotes": [],
                        "dialogue_goals": ["Urgency"],
                        "continuity_notes": ["Del keeps the torch lit"],
                    },
                    {
                        "index": 3,
                        "beat_indices": [1],
                        "beat_summary": "They pause at the ruin gate to reassess.",
                        "story_purpose": "Pause at the ruin gate.",
                        "panel_scale": "small",
                        "panel_shape": "inset",
                        "setting_brief": "Collapsed ruin gate",
                        "character_focus": ["Del", "Vendetta"],
                        "notable_set_dressing": ["Ruin gate"],
                        "notable_quotes": [
                            {
                                "text": "We hold here.",
                                "attribution_context": "Del calls for the party to hold position.",
                            }
                        ],
                        "dialogue_goals": ["Hold position"],
                        "continuity_notes": ["Del still holds the torch"],
                    },
                ],
                "architected_at": "2026-05-04T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return raw_path, entities_path, architecture_path


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
                caption=None,
                voiceover=None,
                chyron="Marsh Edge, Dusk",
                sound_effects=["WHOOSH"],
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
                caption="An hour later, the marsh deepens.",
                voiceover="Del (V.O.): I felt something watching us.",
                chyron=None,
                sound_effects=[],
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
                caption=None,
                voiceover=None,
                chyron=None,
                sound_effects=["CRACKLE", "SPLASH"],
            ),
        ]
    )


def test_write_script_writes_checkpoint_and_normalizes_panel_indices(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "03_script.json"

    def fake_generator(world, architecture, model):
        assert world.title == "Swamp Trouble"
        assert architecture.target_panel_count == 3
        assert model == "qwen3:8b"
        return _valid_payload()

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_architecture_checkpoint_path=architecture_path,
        output_path=output_path,
        model="qwen3:8b",
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
        story_architecture_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        generator=fake_generator,
    )

    assert checkpoint.panel_count == 1
    assert len(checkpoint.generation_errors) == 1
    assert "Architecture alignment failed" in checkpoint.generation_errors[0]


def test_write_script_logs_continuity_error_and_keeps_output(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        broken = _valid_payload()
        broken.panels[1].held_items_before["Del"] = []
        return broken

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_architecture_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
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
        story_architecture_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
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
        story_architecture_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
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
        story_architecture_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        generator=fake_generator,
    )

    assert len(checkpoint.generation_errors) == 1
    assert "missing held_items_before for Del" in checkpoint.generation_errors[0]


def test_write_script_preserves_architect_selected_layout_fields(tmp_path):
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
        story_architecture_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        generator=fake_generator,
    )

    architecture = story_architect.StoryArchitectureCheckpoint.model_validate_json(
        architecture_path.read_text(encoding="utf-8")
    )

    assert checkpoint.panels[0].panel_scale == architecture.panels[0].panel_scale
    assert checkpoint.panels[0].panel_shape == architecture.panels[0].panel_shape
    assert checkpoint.panels[1].panel_scale == architecture.panels[1].panel_scale
    assert checkpoint.panels[1].panel_shape == architecture.panels[1].panel_shape
    assert checkpoint.panel_count == 3


def test_format_entities_for_prompt_excludes_reference_quotes():
    world = scriptwriter.WorldStateInput(
        url="https://example.test/story",
        title="Swamp Trouble",
        author="GM",
        model="qwen3:8b",
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


def test_format_story_architecture_for_prompt_includes_notable_quotes(tmp_path):
    _, _, architecture_path = _write_input_checkpoints(tmp_path)

    architecture = story_architect.StoryArchitectureCheckpoint.model_validate_json(
        architecture_path.read_text(encoding="utf-8")
    )

    prompt_blob = scriptwriter._format_story_architecture_for_prompt(architecture)

    assert '"notable_quotes": [' in prompt_blob
    assert '"text": "Stay close to me."' in prompt_blob
    assert '"attribution_context": "Del warns the party as they enter the marsh."' in prompt_blob


def test_optional_text_layers_preserved_in_checkpoint(tmp_path):
    """Verify that optional text layers (caption, voiceover, chyron, sound_effects) are preserved."""
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_world, _architecture, _model):
        return _valid_payload()

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_architecture_checkpoint_path=architecture_path,
        output_path=tmp_path / "03_script.json",
        generator=fake_generator,
    )

    # Panel 1 has chyron and sound_effects
    assert checkpoint.panels[0].chyron == "Marsh Edge, Dusk"
    assert checkpoint.panels[0].sound_effects == ["WHOOSH"]
    assert checkpoint.panels[0].caption is None
    assert checkpoint.panels[0].voiceover is None

    # Panel 2 has caption and voiceover
    assert checkpoint.panels[1].caption == "An hour later, the marsh deepens."
    assert checkpoint.panels[1].voiceover == "Del (V.O.): I felt something watching us."
    assert checkpoint.panels[1].chyron is None
    assert checkpoint.panels[1].sound_effects == []

    # Panel 3 has sound_effects only
    assert checkpoint.panels[2].sound_effects == ["CRACKLE", "SPLASH"]
    assert checkpoint.panels[2].caption is None
    assert checkpoint.panels[2].voiceover is None
    assert checkpoint.panels[2].chyron is None


def test_optional_text_layers_serialized_in_json(tmp_path):
    """Verify that optional text layers are correctly serialized to JSON."""
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "03_script.json"

    def fake_generator(_world, _architecture, _model):
        return _valid_payload()

    scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_architecture_checkpoint_path=architecture_path,
        output_path=output_path,
        generator=fake_generator,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    
    assert payload["panels"][0]["chyron"] == "Marsh Edge, Dusk"
    assert payload["panels"][0]["sound_effects"] == ["WHOOSH"]
    assert payload["panels"][1]["caption"] == "An hour later, the marsh deepens."
    assert payload["panels"][1]["voiceover"] == "Del (V.O.): I felt something watching us."
    assert payload["panels"][2]["sound_effects"] == ["CRACKLE", "SPLASH"]
