import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import prompter
from model_defaults import DEFAULT_MODEL


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    entities = {
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
        "npcs": [
            {
                "name": "Offscreen NPC",
                "description": "A merchant who is not present in this scene",
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

    script = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "model": DEFAULT_MODEL,
        "panel_count": 2,
        "total_pages": 1,
        "pages": [
            {
                "page_number": 1,
                "panel_count": 2,
                "panels": [
                    {
                        "index": 1,
                        "page_number": 1,
                        "panel_scale": "large",
                        "panel_shape": "wide",
                        "setting": "Marsh edge at dusk",
                        "visual_action": "Del raises a torch while Vendetta scans the reeds.",
                        "dialogue_overlay": ["Del: Keep moving."],
                        "held_items_before": {"Del": [], "Vendetta": []},
                        "held_items_after": {"Del": ["torch"], "Vendetta": []},
                        "narrative_overlays_and_text_direction": [],
                    },
                    {
                        "index": 2,
                        "page_number": 1,
                        "panel_scale": "small",
                        "panel_shape": "inset",
                        "setting": "Narrow marsh path",
                        "visual_action": "Del leads with the torch as Orion marks tracks.",
                        "dialogue_overlay": ["Orion: Tracks ahead."],
                        "held_items_before": {"Del": ["torch"], "Vendetta": []},
                        "held_items_after": {"Del": ["torch"], "Vendetta": []},
                        "narrative_overlays_and_text_direction": [],
                    },
                ],
            }
        ],
        "scripted_at": "2026-05-04T00:00:00+00:00",
    }

    template = {
        "base_style": "Brutalist ink style with heavy shadows.",
        "characters": "Del and Vendetta should stay gaunt, weathered, and instantly recognizable.",
        "color_palette": "Acidic neon pinks, greens, and oranges.",
        "layout_and_composition": "Single comic page with two stacked panels and rough gutters.",
        "lettering_and_dialog": "Jagged handwritten captions with frantic energy.",
        "text_rendering_guide": "Dialogue: jagged speech bubbles. Captions: rough boxes. V.O.: faded text. Chyrons: neon text. SFX: electric bursts.",
    }

    entities_path = tmp_path / "02_entities.json"
    script_path = tmp_path / "03_script.json"
    template_path = tmp_path / "art_direction_template.json"

    entities_path.write_text(json.dumps(entities), encoding="utf-8")
    script_path.write_text(json.dumps(script), encoding="utf-8")
    template_path.write_text(json.dumps(template), encoding="utf-8")

    return entities_path, script_path, template_path


def test_generate_page_prompt_writes_checkpoint(tmp_path):
    entities_path, script_path, template_path = _write_inputs(tmp_path)
    output_path = tmp_path / "04_page_prompt.txt"

    prompt_text = prompter.generate_page_prompt(
        script_checkpoint_path=script_path,
        entities_checkpoint_path=entities_path,
        art_style_template_path=template_path,
        output_path=output_path,
    )

    assert output_path.exists()
    assert prompt_text == output_path.read_text(encoding="utf-8")


def test_generate_page_prompt_contains_interpolated_fields(tmp_path):
    entities_path, script_path, template_path = _write_inputs(tmp_path)

    prompt_text = prompter.generate_page_prompt(
        script_checkpoint_path=script_path,
        entities_checkpoint_path=entities_path,
        art_style_template_path=template_path,
        output_path=tmp_path / "04_page_prompt.txt",
    )

    assert prompt_text.startswith("Swamp Trouble\n\nBase Style: Brutalist ink style with heavy shadows.")
    assert "Characters: Del and Vendetta should stay gaunt, weathered, and instantly recognizable." in prompt_text
    assert "Color Palette: Acidic neon pinks, greens, and oranges." in prompt_text
    assert 'Page elements: Include the title "Swamp Trouble" on the page.' in prompt_text
    assert "Include page number 2 at the bottom of the page." not in prompt_text
    assert "Panel count: 2" in prompt_text
    assert "Panel 1:" in prompt_text
    assert "Panel 2:" in prompt_text
    assert "Del: A druid in mossy robes" in prompt_text
    assert "Vendetta: A wary vampire scout" in prompt_text
    assert "Offscreen NPC: A merchant who is not present in this scene" not in prompt_text


def test_generate_page_prompt_uses_page_number_on_later_pages(tmp_path):
    entities_path, script_path, template_path = _write_inputs(tmp_path)
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["total_pages"] = 2
    script["pages"][0]["page_number"] = 2
    for panel in script["pages"][0]["panels"]:
        panel["page_number"] = 2
    script_path.write_text(json.dumps(script), encoding="utf-8")

    prompt_text = prompter.generate_page_prompt(
        script_checkpoint_path=script_path,
        entities_checkpoint_path=entities_path,
        art_style_template_path=template_path,
        output_path=tmp_path / "04_page_prompt.txt",
    )

    assert prompt_text.startswith("Swamp Trouble\n\nBase Style: Brutalist ink style with heavy shadows.")
    assert "Page elements: Include page number 2 at the bottom of the page." in prompt_text
    assert 'Page elements: Include the title "Swamp Trouble" on the page.' not in prompt_text


def test_generate_page_prompt_fails_when_template_missing(tmp_path):
    entities_path, script_path, _template_path = _write_inputs(tmp_path)

    with pytest.raises(FileNotFoundError, match="Art direction template file not found"):
        prompter.generate_page_prompt(
            script_checkpoint_path=script_path,
            entities_checkpoint_path=entities_path,
            art_style_template_path=tmp_path / "missing_template.json",
            output_path=tmp_path / "04_page_prompt.txt",
        )


def test_generate_page_prompt_uses_custom_page_prompt_template(tmp_path):
    entities_path, script_path, template_path = _write_inputs(tmp_path)
    custom_template = tmp_path / "custom_page_prompt.txt"
    custom_template.write_text(
        "Art: {art_direction}\nChars: {character_details}\nPanels: {panel_count}\n{panel_block}",
        encoding="utf-8",
    )

    prompt_text = prompter.generate_page_prompt(
        script_checkpoint_path=script_path,
        entities_checkpoint_path=entities_path,
        art_style_template_path=template_path,
        output_path=tmp_path / "04_page_prompt.txt",
        page_prompt_template_path=custom_template,
    )

    assert prompt_text.startswith("Art: ")
    assert "Panels: 2" in prompt_text
    assert "Panel 1:" in prompt_text
