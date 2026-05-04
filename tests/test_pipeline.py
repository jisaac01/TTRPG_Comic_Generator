"""Tests for the campaign-aware, versioned ComicPipeline."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import analyzer
import scraper
import scriptwriter
from pipeline import (
    ComicPipeline,
    _create_version_dir,
    _lookup_episode,
    _next_version_name,
    _slugify,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RAW_CHECKPOINT = scraper.RawTextCheckpoint(
    url="https://example.test/story",
    title="Dreadmarsh Crossing",
    author="GM",
    content="Del the Druid crossed the marsh.",
    source_selector="div.story-content",
    scraped_at="2026-05-04T00:00:00+00:00",
)

_WORLD_CHECKPOINT = analyzer.WorldStateCheckpoint(
    url="https://example.test/story",
    title="Dreadmarsh Crossing",
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
    title="Dreadmarsh Crossing",
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
# Helpers
# ---------------------------------------------------------------------------


def _write_version_checkpoints(version_dir: Path) -> None:
    """Write all four checkpoints into a version directory."""
    (version_dir / "01_raw_text.json").write_text(
        _RAW_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "02_entities.json").write_text(
        _WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "03_script.json").write_text(
        _SCRIPT_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "04_page_prompt.txt").write_text(_PAGE_PROMPT, encoding="utf-8")


def _make_episode(campaigns_root: Path, campaign: str, url: str, title: str) -> Path:
    """
    Create a realistic episode folder structure with a v001 of checkpoints
    and register it in the campaign index. Returns the episode dir.
    """
    from pipeline import _register_episode, _slugify

    slug = _slugify(title)
    episode_dir = campaigns_root / campaign / slug
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True, exist_ok=True)
    _write_version_checkpoints(v001)

    meta = {"url": url, "slug": slug, "title": title, "created_at": "2026-05-04T00:00:00+00:00"}
    (episode_dir / "episode_meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    _register_episode(campaigns_root, campaign, url, slug)
    return episode_dir


# ---------------------------------------------------------------------------
# Unit: slugify
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert _slugify("Dreadmarsh Crossing!") == "dreadmarsh-crossing"


def test_slugify_empty_title():
    assert _slugify("") == "episode"


def test_slugify_special_chars():
    assert _slugify("The—Dark & Twisted Fen") == "the-dark-twisted-fen"


# ---------------------------------------------------------------------------
# Unit: version naming
# ---------------------------------------------------------------------------


def test_next_version_name_no_versions(tmp_path):
    assert _next_version_name(tmp_path) == "v001"


def test_next_version_name_increments(tmp_path):
    (tmp_path / "v001").mkdir()
    (tmp_path / "v002").mkdir()
    assert _next_version_name(tmp_path) == "v003"


def test_next_version_name_ignores_non_version_dirs(tmp_path):
    (tmp_path / "v001").mkdir()
    (tmp_path / "episode_meta.json").write_text("{}")
    (tmp_path / "some_other_dir").mkdir()
    assert _next_version_name(tmp_path) == "v002"


# ---------------------------------------------------------------------------
# Unit: version cloning and selective invalidation
# ---------------------------------------------------------------------------


def test_create_version_dir_first_run_no_clone(tmp_path):
    episode_dir = tmp_path / "episodes" / "ep1"
    episode_dir.mkdir(parents=True)

    version_dir, name = _create_version_dir(episode_dir, rerun_from=None)

    assert name == "v001"
    assert version_dir.exists()
    assert list(version_dir.iterdir()) == []


def test_create_version_dir_clones_previous_version(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, name = _create_version_dir(episode_dir, rerun_from=None)

    assert name == "v002"
    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_entities.json").exists()
    assert (version_dir / "03_script.json").exists()
    assert (version_dir / "04_page_prompt.txt").exists()


def test_create_version_dir_rerun_from_prompt_deletes_only_prompt(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _ = _create_version_dir(episode_dir, rerun_from="prompt")

    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_entities.json").exists()
    assert (version_dir / "03_script.json").exists()
    assert not (version_dir / "04_page_prompt.txt").exists()


def test_create_version_dir_rerun_from_analyze_deletes_analyze_onwards(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _ = _create_version_dir(episode_dir, rerun_from="analyze")

    assert (version_dir / "01_raw_text.json").exists()
    assert not (version_dir / "02_entities.json").exists()
    assert not (version_dir / "03_script.json").exists()
    assert not (version_dir / "04_page_prompt.txt").exists()


def test_create_version_dir_rerun_from_scrape_deletes_all(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _ = _create_version_dir(episode_dir, rerun_from="scrape")

    assert not (version_dir / "01_raw_text.json").exists()
    assert not (version_dir / "02_entities.json").exists()
    assert not (version_dir / "03_script.json").exists()
    assert not (version_dir / "04_page_prompt.txt").exists()


# ---------------------------------------------------------------------------
# Unit: campaign index
# ---------------------------------------------------------------------------


def test_lookup_episode_returns_none_for_unknown(tmp_path):
    assert _lookup_episode(tmp_path, "dreadmarsh", "https://example.test/s1") is None


def test_lookup_episode_finds_registered_entry(tmp_path):
    from pipeline import _register_episode

    _register_episode(tmp_path, "dreadmarsh", "https://example.test/s1", "dreadmarsh-crossing")
    assert _lookup_episode(tmp_path, "dreadmarsh", "https://example.test/s1") == "dreadmarsh-crossing"


def test_lookup_episode_ignores_different_campaign(tmp_path):
    from pipeline import _register_episode

    _register_episode(tmp_path, "dreadmarsh", "https://example.test/s1", "ep1")
    assert _lookup_episode(tmp_path, "belowdown", "https://example.test/s1") is None


# ---------------------------------------------------------------------------
# Integration: ComicPipeline.run — first run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_run_creates_campaign_episode_version(tmp_path):
    """A fresh run creates the expected directory tree and index entry."""
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
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

    assert result["version"] == "v001"
    version_dir = Path(result["version_dir"])
    assert version_dir.exists()
    assert version_dir.parent.parent == tmp_path / "dreadmarsh"

    assert _lookup_episode(tmp_path, "dreadmarsh", "https://example.test/story") is not None

    assert "raw_text" in result
    assert "entities" in result
    assert "script" in result
    assert "page_prompt" in result


@pytest.mark.asyncio
async def test_first_run_result_contains_model_dump_dicts(tmp_path):
    """Returned checkpoint values must be plain dicts, not Pydantic models."""
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
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


# ---------------------------------------------------------------------------
# Integration: stage skipping within a version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_skips_all_phases_when_all_checkpoints_exist(tmp_path):
    """If all checkpoints exist in the new version (cloned from previous), no phase runs."""
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.analyze_story") as mock_analyze,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.generate_page_prompt") as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_analyze.assert_not_called()
    mock_script.assert_not_called()
    mock_prompts.assert_not_called()
    assert result["version"] == "v002"


@pytest.mark.asyncio
async def test_rerun_from_analyze_skips_scraper_reruns_rest(tmp_path):
    """rerun_from=analyze: scraper skipped, analyze/script/prompt all run."""
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="analyze",
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
    assert result["version"] == "v002"


@pytest.mark.asyncio
async def test_rerun_from_prompt_only_reruns_prompt(tmp_path):
    """rerun_from=prompt: only generate_page_prompt is called."""
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="prompt",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.analyze_story") as mock_analyze,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_analyze.assert_not_called()
    mock_script.assert_not_called()
    mock_prompts.assert_called_once()
    assert result["version"] == "v002"


# ---------------------------------------------------------------------------
# Integration: URL is canonical episode identity (title changes tolerated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_url_different_title_maps_to_existing_episode(tmp_path):
    """Even if the story title changes, same URL resolves to the same episode folder."""
    episode_dir = _make_episode(
        tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing"
    )
    original_episode_name = episode_dir.name

    changed_title_raw = scraper.RawTextCheckpoint(
        url="https://example.test/story",
        title="The Dreadmarsh Revisited",
        author="GM",
        content="Del the Druid returned to the marsh.",
        source_selector="div.story-content",
        scraped_at="2026-05-04T12:00:00+00:00",
    )

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="scrape",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=changed_title_raw),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    version_dir = Path(result["version_dir"])
    assert version_dir.parent.name == original_episode_name
    assert _lookup_episode(tmp_path, "dreadmarsh", "https://example.test/story") == original_episode_name


# ---------------------------------------------------------------------------
# Integration: multiple campaigns are isolated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_campaigns_are_isolated(tmp_path):
    """Episodes for different campaigns with the same URL are stored separately."""
    url = "https://example.test/shared-story"

    raw_dm = scraper.RawTextCheckpoint(**{**_RAW_CHECKPOINT.model_dump(), "url": url})
    raw_bd = scraper.RawTextCheckpoint(**{**_RAW_CHECKPOINT.model_dump(), "url": url, "title": "Below Story"})

    for campaign, raw in [("dreadmarsh", raw_dm), ("belowdown", raw_bd)]:
        pipeline = ComicPipeline(
            url=url,
            campaign=campaign,
            campaigns_root=tmp_path,
            panel_count=2,
        )
        with (
            patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=raw),
            patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
            patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
            patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
        ):
            await pipeline.run()

    assert (tmp_path / "dreadmarsh").exists()
    assert (tmp_path / "belowdown").exists()
    assert _lookup_episode(tmp_path, "dreadmarsh", url) is not None
    assert _lookup_episode(tmp_path, "belowdown", url) is not None

    dm_episode = tmp_path / "dreadmarsh" / _lookup_episode(tmp_path, "dreadmarsh", url)
    bd_episode = tmp_path / "belowdown" / _lookup_episode(tmp_path, "belowdown", url)
    assert dm_episode.exists()
    assert bd_episode.exists()


# ---------------------------------------------------------------------------
# Integration: art style template resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_campaign_level_art_template_is_used_by_default(tmp_path):
    """Campaign-level art_direction_template.json is passed to Phase 4 when present."""
    template_path = tmp_path / "dreadmarsh" / "art_direction_template.json"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(
        '{"base_style": "Brutalist ink style.", "color_palette": "Black and white only.", '
        '"layout_and_composition": "Single page.", "lettering_and_dialog": "Hand-lettered captions."}',
        encoding="utf-8",
    )

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        await pipeline.run()

    _, kwargs = mock_prompts.call_args
    assert kwargs["art_style_template_path"] == template_path


@pytest.mark.asyncio
async def test_campaign_art_template_is_created_on_first_run(tmp_path):
    """First run auto-creates campaign-level art_direction_template.json when missing."""
    template_path = tmp_path / "dreadmarsh" / "art_direction_template.json"

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        await pipeline.run()

    assert template_path.exists()
    assert template_path.read_text(encoding="utf-8").strip()

    _, kwargs = mock_prompts.call_args
    assert kwargs["art_style_template_path"] == template_path


@pytest.mark.asyncio
async def test_explicit_art_template_overrides_campaign_default(tmp_path):
    """Explicit art_style_template constructor arg takes precedence over campaign default."""
    campaign_template = tmp_path / "dreadmarsh" / "art_direction_template.json"
    campaign_template.parent.mkdir(parents=True, exist_ok=True)
    campaign_template.write_text(
        '{"base_style": "Campaign default.", "color_palette": "Black and white only.", '
        '"layout_and_composition": "Single page.", "lettering_and_dialog": "Hand-lettered captions."}',
        encoding="utf-8",
    )

    explicit_template = tmp_path / "custom_style.json"
    explicit_template.write_text(
        '{"base_style": "Custom override.", "color_palette": "Electric colors.", '
        '"layout_and_composition": "Single page.", "lettering_and_dialog": "Sharp captions."}',
        encoding="utf-8",
    )

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        art_style_template=explicit_template,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        await pipeline.run()

    _, kwargs = mock_prompts.call_args
    assert kwargs["art_style_template_path"] == explicit_template


# ---------------------------------------------------------------------------
# Integration: model forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analysis_model_forwarded(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        analysis_model="llama3:8b",
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT) as mock_analyze,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    _, kwargs = mock_analyze.call_args
    assert kwargs.get("model") == "llama3:8b"


@pytest.mark.asyncio
async def test_script_model_and_panel_count_forwarded(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        script_model="llama3.1:8b",
        panel_count=8,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.analyze_story", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.generate_page_prompt", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    _, kwargs = mock_script.call_args
    assert kwargs.get("model") == "llama3.1:8b"
    assert kwargs.get("panel_count") == 8
