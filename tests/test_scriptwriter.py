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
                "demeanor": "Calm and observant",
            },
            {
                "name": "Vendetta",
                "description": "A wary vampire scout",
                "demeanor": "Cautious and strategic",
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


def test_write_script_raises_when_panel_count_mismatch(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        return scriptwriter.ScriptPayload(panels=_valid_payload().panels[:2])

    with pytest.raises(ValueError, match="Expected exactly 3 panels"):
        scriptwriter.write_script(
            raw_checkpoint_path=raw_path,
            entities_checkpoint_path=entities_path,
            output_path=tmp_path / "03_script.json",
            panel_count=3,
            generator=fake_generator,
        )


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
