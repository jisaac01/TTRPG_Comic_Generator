import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import analyzer
import scraper
import scriptwriter
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

_SCRIPT_CHECKPOINT = scriptwriter.ScriptCheckpoint(
    url="https://example.test/story",
    title="Test Story",
    author="GM",
    model="qwen2.5:7b",
    panel_count=2,
    panels=[
        scriptwriter.Panel(
            index=1,
            setting="Swamp edge at dusk",
            visual_action="Del raises a torch while Vendetta scans the reeds.",
            dialogue_overlay=["Del: Keep moving."],
            held_items_before={"Del": [], "Vendetta": []},
            held_items_after={"Del": ["torch"], "Vendetta": []},
        ),
        scriptwriter.Panel(
            index=2,
            setting="Narrow marsh path",
            visual_action="Del leads with the torch as Orion marks tracks.",
            dialogue_overlay=["Orion: Tracks ahead."],
            held_items_before={"Del": ["torch"], "Vendetta": []},
            held_items_after={"Del": ["torch"], "Vendetta": []},
        ),
    ],
    scripted_at="2026-05-04T00:00:00+00:00",
)

_PAGE_PROMPT = "Single-page comic prompt text"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_calls_scraper_and_analyzer_when_no_checkpoints_exist(tmp_path):
    """Fresh run: no checkpoints exist; scraper, analyzer, and scriptwriter are called."""
    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT) as mock_scrape,
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT) as mock_analyze,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_awaited_once()
    mock_analyze.assert_called_once()
    mock_script.assert_called_once()
    mock_prompts.assert_called_once()
    assert "raw_text" in result
    assert "entities" in result
    assert "script" in result
    assert "page_prompt" in result


@pytest.mark.asyncio
async def test_run_skips_scraper_when_raw_checkpoint_exists(tmp_path):
    """If 01_raw_text.json already exists the scraper must not be called."""
    raw_path = tmp_path / "01_raw_text.json"
    raw_path.write_text(_RAW_CHECKPOINT.model_dump_json(), encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT) as mock_analyze,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_analyze.assert_called_once()
    mock_script.assert_called_once()
    mock_prompts.assert_called_once()
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
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.analyze_story") as mock_analyze,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_analyze.assert_not_called()
    mock_script.assert_called_once()
    mock_prompts.assert_called_once()
    assert result["entities"]["characters"][0]["name"] == "Del"


@pytest.mark.asyncio
async def test_run_skips_scriptwriter_when_script_checkpoint_exists(tmp_path):
    """If 03_script.json already exists the scriptwriter must not be called."""
    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    script_path = tmp_path / "03_script.json"
    raw_path.write_text(_RAW_CHECKPOINT.model_dump_json(), encoding="utf-8")
    entities_path.write_text(_WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8")
    script_path.write_text(_SCRIPT_CHECKPOINT.model_dump_json(), encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
        panel_count=2,
    )

    (tmp_path / "04_page_prompt.txt").write_text(_PAGE_PROMPT, encoding="utf-8")

    with (
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_script.assert_not_called()
    mock_prompts.assert_not_called()
    assert len(result["script"]["panels"]) == 2


@pytest.mark.asyncio
async def test_run_creates_checkpoint_dir_if_missing(tmp_path):
    """ComicPipeline.run() must create the checkpoint directory when absent."""
    nested = tmp_path / "deep" / "nested" / "checkpoints"
    assert not nested.exists()

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=nested,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    assert nested.exists()


@pytest.mark.asyncio
async def test_run_returns_model_dump_for_both_checkpoints(tmp_path):
    """Returned checkpoint values must be plain dicts (model_dump), not Pydantic models."""
    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    assert isinstance(result["raw_text"], dict)
    assert isinstance(result["entities"], dict)
    assert isinstance(result["script"], dict)
    assert isinstance(result["page_prompt"], dict)
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
        panel_count=2,
    )

    with (
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT) as mock_analyze,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    _, kwargs = mock_analyze.call_args
    assert kwargs.get("model") == "llama3:8b"


@pytest.mark.asyncio
async def test_run_passes_script_model_and_panel_count_to_scriptwriter(tmp_path):
    """script_model and panel_count constructor args must be forwarded to write_script."""
    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    raw_path.write_text(_RAW_CHECKPOINT.model_dump_json(), encoding="utf-8")
    entities_path.write_text(_WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
        script_model="llama3.1:8b",
        panel_count=8,
    )

    with (
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    _, kwargs = mock_script.call_args
    assert kwargs.get("model") == "llama3.1:8b"
    assert kwargs.get("panel_count") == 8


@pytest.mark.asyncio
async def test_run_passes_template_and_output_paths_to_prompt_generator(tmp_path):
    """art_style_template and prompts_output constructor args must be forwarded to Phase 4."""
    raw_path = tmp_path / "01_raw_text.json"
    entities_path = tmp_path / "02_entities.json"
    script_path = tmp_path / "03_script.json"
    raw_path.write_text(_RAW_CHECKPOINT.model_dump_json(), encoding="utf-8")
    entities_path.write_text(_WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8")
    script_path.write_text(_SCRIPT_CHECKPOINT.model_dump_json(), encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        checkpoint_dir=tmp_path,
        art_style_template=Path("style.txt"),
        prompts_output=Path("04_prompts_custom.txt"),
        panel_count=2,
    )

    with patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts:
        await pipeline.run()

    _, kwargs = mock_prompts.call_args
    assert kwargs.get("art_style_template_path") == tmp_path / "style.txt"
    assert kwargs.get("output_path") == tmp_path / "04_prompts_custom.txt"
