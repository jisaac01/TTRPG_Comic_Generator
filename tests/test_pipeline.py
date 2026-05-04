import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import analyzer
import scraper
from pipeline import ComicPipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RAW_CHECKPOINT = scraper.RawTextCheckpoint(
    url="https://example.test/story",
    title="Test Story",
    author="GM",
    content="Del the Druid crossed the marsh.",
    source_selector="div.story-content",
    scraped_at="2026-05-04T00:00:00+00:00",
)

_WORLD_CHECKPOINT = analyzer.WorldStateCheckpoint(
    url="https://example.test/story",
    title="Test Story",
    author="GM",
    model="qwen2.5:7b",
    characters=[
        analyzer.Character(
            name="Del",
            description="A druid in mossy robes",
            demeanor="Calm and observant",
        )
    ],
    locations=[
        analyzer.Location(
            name="Dreadmarsh",
            appearance="Foggy marsh with twisted roots",
        )
    ],
    beats=[
        analyzer.StoryBeat(
            index=1,
            text="Del crosses the marsh.",
            quotes=[],
        )
    ],
    analyzed_at="2026-05-04T00:00:00+00:00",
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_calls_scraper_and_analyzer_when_no_checkpoints_exist(tmp_path):
    """Fresh run: neither checkpoint file exists; both scraper and analyzer are called."""
    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT) as mock_scrape,
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT) as mock_analyze,
    ):
        result = await pipeline.run()

    mock_scrape.assert_awaited_once()
    mock_analyze.assert_called_once()
    assert "raw_text" in result
    assert "entities" in result


@pytest.mark.asyncio
async def test_run_skips_scraper_when_raw_checkpoint_exists(tmp_path):
    """If 01_raw_text.json already exists the scraper must not be called."""
    raw_path = tmp_path / "01_raw_text.json"
    raw_path.write_text(_RAW_CHECKPOINT.model_dump_json(), encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT) as mock_analyze,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_analyze.assert_called_once()
    assert result["raw_text"]["title"] == "Test Story"


@pytest.mark.asyncio
async def test_run_skips_analyzer_when_entities_checkpoint_exists(tmp_path):
    """If 02_entities.json already exists the analyzer must not be called."""
    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    raw_path.write_text(_RAW_CHECKPOINT.model_dump_json(), encoding="utf-8")
    entities_path.write_text(_WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.analyze_story") as mock_analyze,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_analyze.assert_not_called()
    assert result["entities"]["characters"][0]["name"] == "Del"


@pytest.mark.asyncio
async def test_run_creates_checkpoint_dir_if_missing(tmp_path):
    """ComicPipeline.run() must create the checkpoint directory when absent."""
    nested = tmp_path / "deep" / "nested" / "checkpoints"
    assert not nested.exists()

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=nested,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
    ):
        await pipeline.run()

    assert nested.exists()


@pytest.mark.asyncio
async def test_run_returns_model_dump_for_both_checkpoints(tmp_path):
    """The returned dict values must be plain dicts (model_dump), not Pydantic models."""
    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
    ):
        result = await pipeline.run()

    assert isinstance(result["raw_text"], dict)
    assert isinstance(result["entities"], dict)
    assert result["raw_text"]["url"] == "https://example.test/story"
    assert result["entities"]["model"] == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_run_passes_model_to_analyzer(tmp_path):
    """The analysis_model constructor parameter must be forwarded to analyze_story."""
    raw_path = tmp_path / "01_raw_text.json"
    raw_path.write_text(_RAW_CHECKPOINT.model_dump_json(), encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
        analysis_model="llama3:8b",
    )

    with patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT) as mock_analyze:
        await pipeline.run()

    _, kwargs = mock_analyze.call_args
    assert kwargs.get("model") == "llama3:8b"
