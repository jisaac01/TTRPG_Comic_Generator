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
        "model": "qwen2.5:7b",
        "characters": [
            {
                "name": "Del",
                "description": "A druid in mossy robes",
            },
            {
                "name": "Vendetta",
                "description": "A wary vampire scout",
            },
        ],
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
                "model": "qwen2.5:7b",
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
                        "must_include": ["The marsh entry"],
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
                        "must_include": ["Del carrying the torch"],
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
                        "must_include": ["Ruin gate"],
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
            ),
        ]
    )


def test_write_script_writes_checkpoint_and_normalizes_panel_indices(tmp_path):
    raw_path, entities_path, architecture_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "03_script.json"

    def fake_generator(content, world, architecture, model):
        assert "torch" in content
        assert world.title == "Swamp Trouble"
        assert architecture.target_panel_count == 3
        assert model == "qwen2.5:7b"
        return _valid_payload()

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        story_architecture_checkpoint_path=architecture_path,
        output_path=output_path,
        model="qwen2.5:7b",
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

    def fake_generator(_content, _world, _architecture, _model):
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

    def fake_generator(_content, _world, _architecture, _model):
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

    def fake_generator(_content, _world, _architecture, _model):
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

    def fake_generator(_content, _world, _architecture, _model):
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

    def fake_generator(_content, _world, _architecture, _model):
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

    def fake_generator(_content, _world, _architecture, _model):
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


def test_format_entities_for_prompt_includes_quotes():
    world = scriptwriter.WorldStateInput(
        url="https://example.test/story",
        title="Swamp Trouble",
        author="GM",
        model="qwen2.5:7b",
        characters=[],
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

    prompt_blob = scriptwriter._format_entities_for_prompt(
        world,
        raw_quotes=[("Stay close to me.", "Del")],
    )

    assert "Reference quotes:" in prompt_blob
    assert 'Del: "Stay close to me."' in prompt_blob


def test_format_entities_for_prompt_empty_raw_quotes():
    world = scriptwriter.WorldStateInput(
        url="https://example.test/story",
        title="Swamp Trouble",
        author="GM",
        model="qwen2.5:7b",
        characters=[],
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

    prompt_blob = scriptwriter._format_entities_for_prompt(world, raw_quotes=[])

    assert "Reference quotes:" in prompt_blob
    assert "- none" in prompt_blob
