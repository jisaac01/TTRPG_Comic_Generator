import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import story_architect


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
        "model": "qwen2.5:7b",
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


def _valid_payload() -> story_architect.StoryArchitecturePayload:
    return story_architect.StoryArchitecturePayload(
        panels=[
            story_architect.ArchitecturePanel(
                index=9,
                beat_indices=[1],
                beat_summary="The party enters the marsh at dusk.",
                story_purpose="Establish the party entering dangerous terrain.",
                panel_scale="large",
                panel_shape="wide",
                setting_brief="Marsh edge at dusk with deep fog.",
                character_focus=["Del", "Vendetta"],
                notable_set_dressing=["The group entering the marsh"],
                notable_quotes=[
                    story_architect.NotableQuote(
                        text="Stay close to me.",
                        speaker="Del",
                        attribution_context="Del warns the party as they enter the marsh.",
                    )
                ],
                dialogue_goals=["Show caution"],
                continuity_notes=["No one is holding the torch yet"],
            ),
            story_architect.ArchitecturePanel(
                index=4,
                beat_indices=[2],
                beat_summary="Del lights a torch to lead the group.",
                story_purpose="Show Del taking control of the route with firelight.",
                panel_scale="medium",
                panel_shape="standard",
                setting_brief="Narrow marsh path with reeds closing in.",
                character_focus=["Del"],
                notable_set_dressing=["Del lighting a torch"],
                notable_quotes=[],
                dialogue_goals=["Commit to moving forward"],
                continuity_notes=["Torch becomes part of ongoing continuity"],
            ),
        ]
    )


def test_architect_story_writes_checkpoint_and_normalizes_indices(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)
    output_path = tmp_path / "02_5_story_architecture.json"

    def fake_generator(content, world, model, panel_count):
        assert "torch" in content
        assert world.title == "Swamp Trouble"
        assert model == "qwen2.5:7b"
        assert panel_count == 2
        return _valid_payload()

    checkpoint = story_architect.architect_story(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=output_path,
        model="qwen2.5:7b",
        panel_count=2,
        generator=fake_generator,
    )

    assert output_path.exists()
    assert checkpoint.target_panel_count == 2
    assert len(checkpoint.panels) == 2
    assert [panel.index for panel in checkpoint.panels] == [1, 2]
    assert checkpoint.panels[0].notable_quotes[0].text == "Stay close to me."
    assert checkpoint.panels[0].notable_quotes[0].speaker == "Del"
    assert checkpoint.generation_errors == []


def test_architect_story_rejects_unreferenced_notable_quote_text(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        payload = _valid_payload()
        payload.panels[0].notable_quotes = [
            story_architect.NotableQuote(
                text="Payment",
                speaker="Bufo",
                attribution_context="Bufo reads an inscription in the vault.",
            )
        ]
        return payload

    checkpoint = story_architect.architect_story(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "02_5_story_architecture.json",
        panel_count=2,
        generator=fake_generator,
    )

    assert checkpoint.panels[0].notable_quotes == []
    assert any("quote text not found in reference quotes" in err for err in checkpoint.generation_errors)


def test_architect_story_logs_uncovered_beats_and_keeps_output(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    def fake_generator(_content, _world, _model, _panel_count):
        payload = _valid_payload()
        payload.panels[1].beat_indices = [1]
        return payload

    checkpoint = story_architect.architect_story(
        raw_checkpoint_path=raw_path,
        entities_checkpoint_path=entities_path,
        output_path=tmp_path / "02_5_story_architecture.json",
        panel_count=2,
        generator=fake_generator,
    )

    assert len(checkpoint.panels) == 2
    assert len(checkpoint.generation_errors) == 1
    assert "missing beat indices [2]" in checkpoint.generation_errors[0]


def test_architect_story_rejects_invalid_panel_count(tmp_path):
    raw_path, entities_path = _write_input_checkpoints(tmp_path)

    try:
        story_architect.architect_story(
            raw_checkpoint_path=raw_path,
            entities_checkpoint_path=entities_path,
            output_path=tmp_path / "02_5_story_architecture.json",
            panel_count=0,
            generator=lambda *_args: _valid_payload(),
        )
    except ValueError as exc:
        assert str(exc) == "panel_count must be >= 1"
    else:
        raise AssertionError("Expected ValueError for invalid panel count")