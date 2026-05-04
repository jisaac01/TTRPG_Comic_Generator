import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import analyzer


def test_analyze_story_writes_entities_checkpoint_and_attributes_quotes(tmp_path):
    raw_input = {
        "url": "https://example.test/story",
        "title": "Swamp Trouble",
        "author": "GM",
        "content": (
            "Del the Druid and Vendetta the Vampire crossed the marsh. "
            "Vendetta said, 'Keep moving.' Orion replied, 'I hear wraiths nearby.'"
        ),
        "source_selector": "div.story-content",
        "scraped_at": "2026-05-04T00:00:00+00:00",
    }
    raw_path = tmp_path / "01_raw_text.json"
    output_path = tmp_path / "02_entities.json"
    raw_path.write_text(json.dumps(raw_input), encoding="utf-8")

    def fake_extractor(content: str, title: str | None, model: str) -> analyzer.ExtractionPayload:
        assert "Vendetta" in content
        assert title == "Swamp Trouble"
        assert model == "qwen2.5:7b"
        return analyzer.ExtractionPayload(
            characters=[
                analyzer.Character(
                    name="Vendetta",
                    description="A pale vampire in travel-worn gear",
                    demeanor="Cautious and decisive",
                )
            ],
            locations=[
                analyzer.Location(
                    name="Dreadmarsh edge",
                    appearance="Foggy marsh with twisted roots",
                )
            ],
            beats=[
                analyzer.StoryBeat(
                    index=7,
                    text="The party crosses the marsh and exchanges warnings.",
                    quotes=[
                        analyzer.Quote(speaker="Vendetta", text="Keep moving."),
                        analyzer.Quote(speaker="Orion", text="I hear wraiths nearby."),
                    ],
                )
            ],
        )

    checkpoint = analyzer.analyze_story(
        raw_checkpoint_path=raw_path,
        output_path=output_path,
        model="qwen2.5:7b",
        extractor=fake_extractor,
    )

    assert checkpoint.characters
    assert checkpoint.characters[0].name == "Vendetta"
    assert checkpoint.beats[0].index == 1
    assert checkpoint.beats[0].quotes[0].speaker == "Vendetta"
    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(payload["characters"]) >= 1
    assert payload["beats"][0]["quotes"][0]["speaker"] == "Vendetta"
