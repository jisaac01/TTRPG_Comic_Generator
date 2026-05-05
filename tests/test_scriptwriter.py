import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import scriptwriter


def _write_input_checkpoints(tmp_path: Path) -> tuple[Path, Path]:
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
                "text": "The party enters the marsh at dusk.",
                "quotes": [],
            }
        ],
        "analyzed_at": "2026-05-04T00:00:00+00:00",
    }

    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    raw_path.write_text(json.dumps(raw_input), encoding="utf-8")
    entities_path.write_text(json.dumps(entities_input), encoding="utf-8")
    return raw_path, entities_path


def _valid_payload() -> scriptwriter.ScriptPayload:
    return scriptwriter.ScriptPayload(
        panels=[
            scriptwriter.Panel(
                index=99,
                setting="Marsh edge",
                visual_action="Del lights a torch while Vendetta watches the reeds.",
                dialogue_overlay=["Del: Stay close."],
                held_items_before={"Del": [], "Vendetta": []},
                held_items_after={"Del": ["torch"], "Vendetta": []},
            ),
            scriptwriter.Panel(
                index=17,
                setting="Narrow marsh path",
                visual_action="Del leads with the torch and Vendetta tracks footprints.",
                dialogue_overlay=["Vendetta: Fresh tracks."],
                held_items_before={"Del": ["torch"], "Vendetta": []},
                held_items_after={"Del": ["torch"], "Vendetta": ["map"]},
            ),
            scriptwriter.Panel(
                index=3,
                setting="Collapsed ruin gate",
                visual_action="Vendetta studies the map as Del keeps the torch raised.",
                dialogue_overlay=["Del: We hold here."],
                held_items_before={"Del": ["torch"], "Vendetta": ["map"]},
                held_items_after={"Del": ["torch"], "Vendetta": ["map"]},
            ),
        ]
    )


def test_write_script_writes_checkpoint_and_normalizes_panel_indices(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "03_script.json"

    def fake_generator(content, world, model, panel_count):
        assert "torch" in content
        assert world.title == "Swamp Trouble"
        assert model == "qwen2.5:7b"
        assert panel_count == 3
        return _valid_payload()

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=output_path,
        model="qwen2.5:7b",
        panel_count=3,
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
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        return scriptwriter.ScriptPayload(panels=_valid_payload().panels[:1])

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "03_script.json",
        panel_count=3,
        max_generation_attempts=1,
        generator=fake_generator,
    )

    assert checkpoint.panel_count == 1
    assert len(checkpoint.generation_errors) == 0


def test_write_script_retries_on_continuity_error_and_succeeds(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)
    attempts = {"count": 0}

    def fake_generator(_content, _world, _model, _panel_count):
        attempts["count"] += 1
        if attempts["count"] == 1:
            broken = _valid_payload()
            broken.panels[1].held_items_before["Del"] = []
            return broken
        return _valid_payload()

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "03_script.json",
        panel_count=3,
        max_generation_attempts=3,
        generator=fake_generator,
    )

    assert attempts["count"] == 2
    assert len(checkpoint.panels) == 3
    assert len(checkpoint.generation_errors) == 0


def test_write_script_raises_on_continuity_break(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        broken = _valid_payload()
        broken.panels[1].held_items_before["Del"] = []
        return broken

    with pytest.raises(ValueError, match="Continuity break"):
        scriptwriter.write_script(
            raw_checkpoint_path=raw_path,
            entities_checkpoint_path=entities_path,
            output_path=tmp_path / "03_script.json",
            panel_count=3,
            generator=fake_generator,
        )


def test_write_script_allows_added_items_between_panels(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        payload = _valid_payload()
        payload.panels[1].held_items_before["Del"] = ["torch", "amulet"]
        return payload

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "03_script.json",
        panel_count=3,
        generator=fake_generator,
    )

    assert checkpoint.panels[1].held_items_before["Del"] == ["torch", "amulet"]


def test_write_script_allows_missing_character_when_inventory_empty(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        payload = _valid_payload()
        payload.panels[0].held_items_after["Vendetta"] = []
        payload.panels[1].held_items_before.pop("Vendetta", None)
        return payload

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "03_script.json",
        panel_count=3,
        generator=fake_generator,
    )

    assert checkpoint.panel_count == 3


def test_write_script_raises_when_missing_character_with_items(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        payload = _valid_payload()
        payload.panels[0].held_items_after["Del"] = ["torch"]
        payload.panels[1].held_items_before.pop("Del", None)
        return payload

    with pytest.raises(ValueError, match="missing held_items_before for Del"):
        scriptwriter.write_script(
            raw_checkpoint_path=raw_path,
            entities_checkpoint_path=entities_path,
            output_path=tmp_path / "03_script.json",
            panel_count=3,
            generator=fake_generator,
        )


def test_write_script_passes_panel_count_to_generator(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    payload = json.loads(entities_path.read_text(encoding="utf-8"))
    payload["beats"] = [
        {"index": 1, "text": "Beat one.", "quotes": []},
        {"index": 2, "text": "Beat two.", "quotes": []},
        {"index": 3, "text": "Beat three.", "quotes": []},
        {"index": 4, "text": "Beat four.", "quotes": []},
    ]
    entities_path.write_text(json.dumps(payload), encoding="utf-8")

    calls = {"panel_target": None}

    def fake_generator(_content, _world, _model, panel_count):
        calls["panel_target"] = panel_count
        panels = _valid_payload().panels
        fourth = scriptwriter.Panel(
            index=42,
            setting="Broken watchtower",
            visual_action="Del and Vendetta scout from the tower over the foggy marsh.",
            dialogue_overlay=["Vendetta: Lights to the east."],
            held_items_before={"Del": ["torch"], "Vendetta": ["map"]},
            held_items_after={"Del": ["torch"], "Vendetta": ["map"]},
        )
        return scriptwriter.ScriptPayload(panels=[*panels, fourth])

    checkpoint = scriptwriter.write_script(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "03_script.json",
        panel_count=3,
        generator=fake_generator,
    )

    assert calls["panel_target"] == 3
    assert checkpoint.panel_count == 4
    assert len(checkpoint.panels) == 4


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
                text="The party reaches the old bridge.",
                quotes=[scriptwriter.Quote(speaker="Del", text="Stay close to me.")],
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


def test_format_entities_for_prompt_ignores_beat_quotes_when_raw_empty():
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
                text="The party reaches the old bridge.",
                quotes=[scriptwriter.Quote(speaker="Del", text="Stay close to me.")],
            )
        ],
        analyzed_at="2026-05-04T00:00:00+00:00",
    )

    prompt_blob = scriptwriter._format_entities_for_prompt(world, raw_quotes=[])

    assert "Reference quotes:" in prompt_blob
    assert "- none" in prompt_blob
