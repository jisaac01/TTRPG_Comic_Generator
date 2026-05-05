import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import prompter


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    entities = {
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

    script = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "model": "qwen2.5:7b",
        "panel_count": 2,
        "panels": [
            {
                "index": 1,
                "setting": "Marsh edge at dusk",
                "visual_action": "Del raises a torch while Vendetta scans the reeds.",
                "dialogue_overlay": ["Del: Keep moving."],
                "held_items_before": {"Del": [], "Vendetta": []},
                "held_items_after": {"Del": ["torch"], "Vendetta": []},
            },
            {
                "index": 2,
                "setting": "Narrow marsh path",
                "visual_action": "Del leads with the torch as Orion marks tracks.",
                "dialogue_overlay": ["Orion: Tracks ahead."],
                "held_items_before": {"Del": ["torch"], "Vendetta": []},
                "held_items_after": {"Del": ["torch"], "Vendetta": []},
            },
        ],
        "scripted_at": "2026-05-04T00:00:00+00:00",
    }

    template = {
        "base_style": "Brutalist ink style with heavy shadows.",
        "color_palette": "Acidic neon pinks, greens, and oranges.",
        "layout_and_composition": "Single comic page with two stacked panels and rough gutters.",
        "lettering_and_dialog": "Jagged handwritten captions with frantic energy.",
    }

    entities_path = tmp_path / "02_entities.json"
    script_path = tmp_path / "03_script.json"
    template_path = tmp_path / "art_direction_template.json"

    entities_path.write_text(json.dumps(entities), encoding="utf-8")
    script_path.write_text(json.dumps(script), encoding="utf-8")
    template_path.write_text(json.dumps(template), encoding="utf-8")

    return entities_path, script_path, template_path


def test_generate_image_prompts_writes_checkpoint(tmp_path):
    entities_path, script_path, template_path = _write_inputs(tmp_path)
    output_path = tmp_path / "04_page_prompt.txt"

    prompt_text = prompter.generate_page_prompt(
        script_checkpoint_path=script_path,
        entities_checkpoint_path=entities_path,
        art_style_template_path=template_path,
        output_path=output_path,
    )

    assert output_path.exists()
    assert "Panel 1:" in prompt_text
    assert "Panel 2:" in prompt_text
    assert "Base Style: Brutalist ink style with heavy shadows." in prompt_text
    assert "Color Palette: Acidic neon pinks, greens, and oranges." in prompt_text
    assert "Layout & Composition: Single comic page with two stacked panels and rough gutters." in prompt_text
    assert "Lettering & Dialog: Jagged handwritten captions with frantic energy." in prompt_text
    assert "- Setting: Marsh edge at dusk" in prompt_text
    assert "Character details (apply to every panel):" in prompt_text
    assert "Rendering constraints:" not in prompt_text
    saved = output_path.read_text(encoding="utf-8")
    assert saved == prompt_text


def test_generate_image_prompts_fails_when_template_missing(tmp_path):
    entities_path, script_path, _template_path = _write_inputs(tmp_path)

    with pytest.raises(FileNotFoundError, match="Art direction template file not found"):
        prompter.generate_page_prompt(
            script_checkpoint_path=script_path,
            entities_checkpoint_path=entities_path,
            art_style_template_path=tmp_path / "missing_template.json",
            output_path=tmp_path / "04_page_prompt.txt",
        )
