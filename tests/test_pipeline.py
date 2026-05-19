"""Tests for the campaign-aware, versioned ComicPipeline."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import entities
from model_defaults import DEFAULT_MODEL
from prompter import DEFAULT_ART_DIRECTION_TEMPLATE_PATH
from prompt_templates import (
    DEFAULT_PROMPTS_DIR,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    PROMPT_TEMPLATE_FILENAMES,
    MASTER_BEATER_SYSTEM_PROMPT_FILENAME,
    MASTER_BEATER_USER_PROMPT_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)
import scraper
import scriptwriter
import master_beater
from style_integrator import StyleIntegrationPartialFailure
from pipeline import (
    ComicPipeline,
    _create_version_dir,
    _lookup_episode,
    _next_version_name,
    _slugify,
)
from prompt_saver import prepare_scriptwriter_prompts

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RAW_CHECKPOINT = scraper.RawTextCheckpoint(
    url="https://example.test/story",
    title="Dreadmarsh Crossing",
    author="GM",
    content="Del the Druid crossed the marsh.",
    recap_variants={
        "standard": "Del the Druid crossed the marsh.",
        "short": "Del crossed the marsh.",
        "alternate": "Del and crew crossed the marsh.",
        "long": "Del the Druid crossed the marsh and charted every bog path.",
    },
    selected_recap="standard",
    source_selector="div.story-content",
    scraped_at="2026-05-04T00:00:00+00:00",
)

_WORLD_CHECKPOINT = entities.WorldStateCheckpoint(
    url="https://example.test/story",
    title="Dreadmarsh Crossing",
    author="GM",
    model=DEFAULT_MODEL,
    player_characters=[
        entities.Character(
            name="Del",
            description="A druid in mossy robes",
        )
    ],
    npcs=[],
    locations=[
        entities.Location(
            name="Dreadmarsh",
            appearance="Foggy marsh with twisted roots",
        )
    ],
    beats=[
        entities.StoryBeat(
            index=1,
            beat="Del crosses the marsh.",
            highlights=["Del crosses the marsh."],
        )
    ],
    analyzed_at="2026-05-04T00:00:00+00:00",
)

_SCRIPT_CHECKPOINT = scriptwriter.ScriptCheckpoint(
    url="https://example.test/story",
    title="Dreadmarsh Crossing",
    author="GM",
    model=DEFAULT_MODEL,
    panel_count=2,
    total_pages=1,
    pages=[
        scriptwriter.Page(
            page_number=1,
            panel_count=2,
            panels=[
                scriptwriter.Panel(
                    index=1,
                    page_number=1,
                    panel_scale="large",
                    panel_shape="wide",
                    setting="Swamp edge at dusk",
                    visual_action="Del raises a torch while Vendetta scans the reeds.",
                    dialogue_overlay=["Del: Keep moving."],
                    held_items_before={"Del": [], "Vendetta": []},
                    held_items_after={"Del": ["torch"], "Vendetta": []},
                    narrative_overlays_and_text_direction=[
                        "CAPTION: The companions stand at the edge of the marsh, preparing to venture into the unknown.",
                        "V.O.: Del (V.O.): We must reach the far bank before nightfall.",
                        "CHYRON: Dreadmarsh - Evening",
                    ],
                ),
                scriptwriter.Panel(
                    index=2,
                    page_number=1,
                    panel_scale="medium",
                    panel_shape="standard",
                    setting="Narrow marsh path",
                    visual_action="Del leads with the torch as Orion marks tracks.",
                    dialogue_overlay=["Orion: Tracks ahead."],
                    held_items_before={"Del": ["torch"], "Vendetta": []},
                    held_items_after={"Del": ["torch"], "Vendetta": []},
                    narrative_overlays_and_text_direction=[
                        "CAPTION: Following the torchlight deeper into the maze of reeds.",
                        "V.O.: Vendetta (V.O.): Something moves in the darkness.",
                        "CHYRON: Deeper In",
                    ],
                ),
            ],
        )
    ],
    scripted_at="2026-05-04T00:00:00+00:00",
)

_STORY_BIBLE_CHECKPOINT = master_beater.StoryBibleCheckpoint(
    url="https://example.test/story",
    title="Dreadmarsh Crossing",
    author="GM",
    model=DEFAULT_MODEL,
    scene_count=2,
    story_bible="""Scene 1:
Del the Druid raises her torch as she and Vendetta stand at the edge of Dreadmarsh. The path ahead winds through reeds taller than a person, their silhouettes ghostly in the dusk light. Del's voice is steady but urgent. \"Keep moving. We need to reach the far bank before full dark.\"

Scene 2:
Del moves forward with the torch held high, Vendetta at her shoulder. The ground is treacherous, mud sucking at their boots. Vendetta scans the reeds around them, looking for threats. \"Tracks ahead,\" she whispers. The marsh air is thick and cold.""",
    generation_errors=[],
    created_at="2026-05-04T00:00:00+00:00",
)

_PAGE_PROMPT = "Single-page comic prompt text"

_STYLED_SCRIPT_CHECKPOINT = scriptwriter.ScriptCheckpoint(
    url="https://example.test/story",
    title="Dreadmarsh Crossing",
    author="GM",
    model=DEFAULT_MODEL,
    panel_count=2,
    total_pages=1,
    pages=[
        scriptwriter.Page(
            page_number=1,
            panel_count=2,
            panels=[
                scriptwriter.Panel(
                    index=1,
                    page_number=1,
                    panel_scale="large",
                    panel_shape="wide",
                    setting="A scribbly swamp edge at wobbly dusk",
                    visual_action="A scratchy Del raises a wobbly torch while Vendetta scans the reeds.",
                    dialogue_overlay=["Del: Keep moving."],
                    held_items_before={"Del": [], "Vendetta": []},
                    held_items_after={"Del": ["torch"], "Vendetta": []},
                    narrative_overlays_and_text_direction=[
                        "CAPTION: The companions stand at the edge of the marsh, preparing to venture into the unknown.",
                        "V.O.: Del (V.O.): We must reach the far bank before nightfall.",
                        "CHYRON: Dreadmarsh - Evening",
                    ],
                ),
                scriptwriter.Panel(
                    index=2,
                    page_number=1,
                    panel_scale="medium",
                    panel_shape="standard",
                    setting="A crooked narrow marsh path",
                    visual_action="A wobbly Del leads with the torch as Orion marks tracks.",
                    dialogue_overlay=["Orion: Tracks ahead."],
                    held_items_before={"Del": ["torch"], "Vendetta": []},
                    held_items_after={"Del": ["torch"], "Vendetta": []},
                    narrative_overlays_and_text_direction=[
                        "CAPTION: Following the torchlight deeper into the maze of reeds.",
                        "V.O.: Vendetta (V.O.): Something moves in the darkness.",
                        "CHYRON: Deeper In",
                    ],
                ),
            ],
        )
    ],
    scripted_at="2026-05-04T00:00:00+00:00",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_version_checkpoints(version_dir: Path) -> None:
    """Write all checkpoints into a version directory."""
    prompts_dir = version_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "01_raw_text.json").write_text(
        _RAW_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "02_entities.json").write_text(
        _WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "02_5_story_bible.json").write_text(
        _STORY_BIBLE_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "02_6_story_bible_page_001.json").write_text(
        _STORY_BIBLE_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "03_script_page_001.json").write_text(
        _SCRIPT_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "03_5_styled_script_page_001.json").write_text(
        _STYLED_SCRIPT_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "04_page_1_prompt.txt").write_text(_PAGE_PROMPT, encoding="utf-8")


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


def _version_dir_from_result(result: dict[str, object]) -> Path:
    return Path(cast(str, result["version_dir"]))


def _checkpoint_dict(result: dict[str, object], key: str) -> dict[str, object]:
    return cast(dict[str, object], result[key])


# ---------------------------------------------------------------------------
# Unit: slugify
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert _slugify("Dreadmarsh Crossing!") == "dreadmarsh-crossing"


def test_slugify_empty_title():
    assert _slugify("") == "episode"


def test_slugify_special_chars():
    assert _slugify("The—Dark & Twisted Fen") == "the-dark-twisted-fen"


def test_prepare_scriptwriter_prompts_adds_first_page_directive_only_on_page_one(tmp_path):
    version_dir = tmp_path / "v001"
    world = scriptwriter.WorldStateInput(
        url="https://example.test/story",
        title="Dreadmarsh Crossing",
        author="GM",
        model=DEFAULT_MODEL,
        player_characters=_WORLD_CHECKPOINT.player_characters,
        npcs=_WORLD_CHECKPOINT.npcs,
        locations=_WORLD_CHECKPOINT.locations,
        beats=_WORLD_CHECKPOINT.beats,
        analyzed_at=_WORLD_CHECKPOINT.analyzed_at,
    )

    _, first_page_user_prompt = prepare_scriptwriter_prompts(
        version_dir=version_dir,
        world=world,
        story_bible=_STORY_BIBLE_CHECKPOINT,
        page_number=1,
        output_suffix="page_001",
    )
    _, second_page_user_prompt = prepare_scriptwriter_prompts(
        version_dir=version_dir,
        world=world,
        story_bible=_STORY_BIBLE_CHECKPOINT,
        page_number=2,
        output_suffix="page_002",
    )

    assert "For page 1 only: Include a CAPTION narration entry" in first_page_user_prompt
    assert "For page 1 only: Include a CAPTION narration entry" not in second_page_user_prompt


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
    assert (version_dir / "02_5_story_bible.json").exists()
    assert (version_dir / "03_script_page_001.json").exists()
    assert (version_dir / "03_5_styled_script_page_001.json").exists()
    assert (version_dir / "04_page_1_prompt.txt").exists()


def test_create_version_dir_rerun_from_prompt_deletes_only_prompt(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _ = _create_version_dir(episode_dir, rerun_from="prompt")

    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_entities.json").exists()
    assert (version_dir / "02_5_story_bible.json").exists()
    assert (version_dir / "03_script_page_001.json").exists()
    assert (version_dir / "03_5_styled_script_page_001.json").exists()
    assert not (version_dir / "04_page_1_prompt.txt").exists()


def test_create_version_dir_rerun_from_beater_deletes_beater_onwards(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _ = _create_version_dir(episode_dir, rerun_from="beater")

    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_entities.json").exists()
    assert not (version_dir / "02_5_story_bible.json").exists()
    assert not (version_dir / "03_script_page_001.json").exists()
    assert not (version_dir / "03_5_styled_script_page_001.json").exists()
    assert not (version_dir / "04_page_1_prompt.txt").exists()


def test_create_version_dir_rerun_from_scrape_deletes_all(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _ = _create_version_dir(episode_dir, rerun_from="scrape")

    assert not (version_dir / "01_raw_text.json").exists()
    assert not (version_dir / "02_entities.json").exists()
    assert not (version_dir / "03_script_page_001.json").exists()
    assert not (version_dir / "03_5_styled_script_page_001.json").exists()
    assert not (version_dir / "04_page_1_prompt.txt").exists()


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
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT) as mock_entities,
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_awaited_once()
    assert mock_scrape.await_args is not None
    kwargs = mock_scrape.await_args.kwargs
    assert kwargs["recap_version"] == "standard"
    mock_entities.assert_called_once()
    mock_architect.assert_called_once()
    mock_script.assert_called_once()
    mock_prompts.assert_called_once()

    assert result["version"] == "v001"
    version_dir = _version_dir_from_result(result)
    assert version_dir.exists()
    assert version_dir.parent.parent == tmp_path / "dreadmarsh"

    assert _lookup_episode(tmp_path, "dreadmarsh", "https://example.test/story") is not None

    assert "raw_text" in result
    assert "entities" in result
    assert "story_bible" in result
    assert "script" in result
    assert "styled_script" in result
    assert "page_prompt" in result


@pytest.mark.asyncio
async def test_first_run_bootstraps_campaign_prompt_templates_and_copies_version_prompts(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    for filename in PROMPT_TEMPLATE_FILENAMES:
        campaign_prompt = tmp_path / "dreadmarsh" / filename
        version_prompt = version_dir / "prompts" / filename
        assert campaign_prompt.exists()
        assert version_prompt.exists()
        assert campaign_prompt.read_text(encoding="utf-8") == (
            DEFAULT_PROMPTS_DIR / filename
        ).read_text(encoding="utf-8")
        assert version_prompt.read_text(encoding="utf-8") == campaign_prompt.read_text(encoding="utf-8")

    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "master_beater_system_FINAL.txt"
    ).read_text(encoding="utf-8")
    assert architect_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "master_beater_user_FINAL.txt"
    ).read_text(encoding="utf-8")

    _, script_kwargs = mock_script.call_args
    assert script_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "scriptwriter_system_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")
    assert script_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "scriptwriter_user_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")

    _, style_kwargs = mock_integrate.call_args
    assert style_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "style_integrator_system_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")
    assert style_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "style_integrator_user_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")

    _, prompt_kwargs = mock_prompts.call_args
    assert prompt_kwargs["template_path"] == version_dir / "prompts" / PAGE_PROMPT_TEMPLATE_FILENAME


@pytest.mark.asyncio
async def test_explicit_prompt_overrides_are_copied_into_version(tmp_path):
    page_prompt_template = tmp_path / "custom_page_prompt.txt"
    architect_system = tmp_path / "custom_architect_system.txt"
    architect_user = tmp_path / "custom_architect_user.txt"
    style_system = tmp_path / "custom_style_system.txt"
    style_user = tmp_path / "custom_style_user.txt"
    system_prompt = tmp_path / "custom_system.txt"
    user_prompt = tmp_path / "custom_user.txt"
    architect_system.write_text("ARCHITECT SYSTEM OVERRIDE", encoding="utf-8")
    architect_user.write_text("ARCHITECT USER OVERRIDE", encoding="utf-8")
    system_prompt.write_text("SYSTEM OVERRIDE", encoding="utf-8")
    user_prompt.write_text("USER OVERRIDE", encoding="utf-8")
    style_system.write_text("STYLE SYSTEM OVERRIDE", encoding="utf-8")
    style_user.write_text("STYLE USER OVERRIDE", encoding="utf-8")
    page_prompt_template.write_text("CUSTOM PAGE PROMPT: {panel_count}", encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        master_beater_system_prompt=architect_system,
        master_beater_user_prompt=architect_user,
        scriptwriter_system_prompt=system_prompt,
        scriptwriter_user_prompt=user_prompt,
        style_integrator_system_prompt=style_system,
        style_integrator_user_prompt=style_user,
        page_prompt_template=page_prompt_template,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    prompts_dir = version_dir / "prompts"
    assert (prompts_dir / MASTER_BEATER_SYSTEM_PROMPT_FILENAME).read_text(encoding="utf-8") == "ARCHITECT SYSTEM OVERRIDE"
    assert (prompts_dir / MASTER_BEATER_USER_PROMPT_FILENAME).read_text(encoding="utf-8") == "ARCHITECT USER OVERRIDE"
    assert (prompts_dir / SCRIPTWRITER_SYSTEM_PROMPT_FILENAME).read_text(encoding="utf-8") == "SYSTEM OVERRIDE"
    assert (prompts_dir / SCRIPTWRITER_USER_PROMPT_FILENAME).read_text(encoding="utf-8") == "USER OVERRIDE"
    assert (prompts_dir / STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME).read_text(encoding="utf-8") == "STYLE SYSTEM OVERRIDE"
    assert (prompts_dir / STYLE_INTEGRATOR_USER_PROMPT_FILENAME).read_text(encoding="utf-8") == "STYLE USER OVERRIDE"
    assert (prompts_dir / PAGE_PROMPT_TEMPLATE_FILENAME).read_text(encoding="utf-8") == "CUSTOM PAGE PROMPT: {panel_count}"

    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "master_beater_system_FINAL.txt"
    ).read_text(encoding="utf-8")
    assert architect_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "master_beater_user_FINAL.txt"
    ).read_text(encoding="utf-8")

    _, script_kwargs = mock_script.call_args
    assert script_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "scriptwriter_system_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")
    assert script_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "scriptwriter_user_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")

    _, style_kwargs = mock_integrate.call_args
    assert style_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "style_integrator_system_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")
    assert style_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "style_integrator_user_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")

    _, prompt_kwargs = mock_prompts.call_args
    assert prompt_kwargs["template_path"] == version_dir / "prompts" / PAGE_PROMPT_TEMPLATE_FILENAME


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
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    assert isinstance(result["raw_text"], dict)
    assert isinstance(result["entities"], dict)
    assert isinstance(result["story_bible"], dict)
    assert isinstance(result["script"], list)
    assert isinstance(result["styled_script"], list)
    assert isinstance(result["page_prompt"], dict)
    assert result["errors"] == []
    assert _checkpoint_dict(result, "raw_text")["url"] == "https://example.test/story"
    assert _checkpoint_dict(result, "entities")["model"] == DEFAULT_MODEL


@pytest.mark.asyncio
async def test_first_run_forwards_explicit_recap_version_to_scraper(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        recap_version="alt",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT) as mock_scrape,
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    assert mock_scrape.await_args is not None
    kwargs = mock_scrape.await_args.kwargs
    assert kwargs["recap_version"] == "alternate"


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
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template") as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_not_called()
    mock_script.assert_not_called()
    mock_prompts.assert_not_called()
    assert result["version"] == "v002"


@pytest.mark.asyncio
async def test_cached_raw_recap_switch_updates_content_and_reruns_downstream(tmp_path):
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        recap_version="short",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT) as mock_entities,
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_called_once()
    mock_architect.assert_not_called()
    mock_script.assert_called_once()
    mock_prompts.assert_called_once()

    raw_path = _version_dir_from_result(result) / "01_raw_text.json"
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw_payload["selected_recap"] == "short"
    assert raw_payload["content"] == _RAW_CHECKPOINT.recap_variants["short"]


@pytest.mark.asyncio
async def test_rerun_from_entities_skips_scraper_reruns_rest(tmp_path):
    """rerun_from=entities: scraper skipped, entities/script/prompt all run."""
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="entities",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT) as mock_entities,
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_called_once()
    mock_architect.assert_called_once()
    mock_script.assert_called_once()
    mock_integrate.assert_called_once()
    mock_prompts.assert_called_once()
    assert result["version"] == "v002"


@pytest.mark.asyncio
async def test_rerun_from_architect_reruns_architect_and_downstream(tmp_path):
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="beater",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_called_once()
    mock_script.assert_called_once()
    mock_integrate.assert_called_once()
    mock_prompts.assert_called_once()
    assert result["version"] == "v002"


@pytest.mark.asyncio
async def test_rerun_from_style_only_reruns_style_and_prompt(tmp_path):
    """rerun_from=style: only style integration and prompt generation are called."""
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="style",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_not_called()
    mock_script.assert_not_called()
    mock_integrate.assert_called_once()
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
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.integrate_style") as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_not_called()
    mock_script.assert_not_called()
    mock_integrate.assert_not_called()
    mock_prompts.assert_called_once()
    assert result["version"] == "v002"


@pytest.mark.asyncio
async def test_skip_style_bypasses_integrator_and_prompts_from_script(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        skip_style=True,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style") as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_integrate.assert_not_called()
    mock_prompts.assert_called_once()
    _, prompt_kwargs = mock_prompts.call_args
    assert prompt_kwargs["output_suffix"] == "page_001"
    assert result["styled_script"] is not None
    assert result["styled_script"] == result["script"]


@pytest.mark.asyncio
async def test_rerun_from_style_with_skip_style_reruns_prompt_only(tmp_path):
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="style",
        skip_style=True,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.integrate_style") as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_not_called()
    mock_script.assert_not_called()
    mock_integrate.assert_not_called()
    mock_prompts.assert_called_once()
    _, prompt_kwargs = mock_prompts.call_args
    assert prompt_kwargs["output_suffix"] == "page_001"
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
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
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
            patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
            patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
            patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
            patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
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
        '{"base_style": "Brutalist ink style.", "characters": "Consistent iconic silhouettes.", '
        '"color_palette": "Black and white only.", "layout_and_composition": "Single page.", '
        '"lettering_and_dialog": "Hand-lettered captions.", '
        '"text_rendering_guide": "Use balloons for dialogue, boxes for captions, and distinct SFX lettering."}',
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
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    _, kwargs = mock_integrate.call_args
    version_template_path = _version_dir_from_result(result) / "art_direction_template.json"
    assert kwargs["art_style_template_path"] == version_template_path
    assert version_template_path.read_text(encoding="utf-8") == template_path.read_text(encoding="utf-8")


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
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    assert template_path.exists()
    assert template_path.read_text(encoding="utf-8") == DEFAULT_ART_DIRECTION_TEMPLATE_PATH.read_text(
        encoding="utf-8"
    )

    _, kwargs = mock_integrate.call_args
    version_template_path = _version_dir_from_result(result) / "art_direction_template.json"
    assert kwargs["art_style_template_path"] == version_template_path
    assert version_template_path.read_text(encoding="utf-8") == template_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_explicit_art_template_overrides_campaign_default(tmp_path):
    """Explicit art_style_template constructor arg takes precedence over campaign default."""
    campaign_template = tmp_path / "dreadmarsh" / "art_direction_template.json"
    campaign_template.parent.mkdir(parents=True, exist_ok=True)
    campaign_template.write_text(
        '{"base_style": "Campaign default.", "characters": "Consistent campaign cast designs.", '
        '"color_palette": "Black and white only.", "layout_and_composition": "Single page.", '
        '"lettering_and_dialog": "Hand-lettered captions.", '
        '"text_rendering_guide": "Dialogue balloons, caption boxes, and expressive SFX treatment."}',
        encoding="utf-8",
    )

    explicit_template = tmp_path / "custom_style.json"
    explicit_template.write_text(
        '{"base_style": "Custom override.", "characters": "Custom stylized cast silhouettes.", '
        '"color_palette": "Electric colors.", "layout_and_composition": "Single page.", '
        '"lettering_and_dialog": "Sharp captions.", '
        '"text_rendering_guide": "Distinct dialogue balloons, caption bars, and energetic SFX typography."}',
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
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    _, kwargs = mock_integrate.call_args
    version_template_path = _version_dir_from_result(result) / "art_direction_template.json"
    assert kwargs["art_style_template_path"] == version_template_path
    assert version_template_path.read_text(encoding="utf-8") == explicit_template.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Integration: model forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entities_phase_uses_scraper_direct_label(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT) as mock_entities,
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    _, kwargs = mock_entities.call_args
    assert kwargs.get("model_label") == "scraper-direct"


@pytest.mark.asyncio
async def test_script_model_and_panel_count_forwarded(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        beater_model="llama3.1:8b",
        script_model="llama3.1:8b",
        panel_count=8,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs.get("model") == "llama3.1:8b"
    assert architect_kwargs.get("scene_count") == 8

    _, kwargs = mock_script.call_args
    assert kwargs.get("model") == "llama3.1:8b"
    assert kwargs.get("story_bible_checkpoint_path").name == "02_6_story_bible_page_001.json"


@pytest.mark.asyncio
async def test_beater_prompt_uses_total_scene_count(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        total_pages=3,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs.get("scene_count") == 6

    version_dir = _version_dir_from_result(result)
    rendered_prompt = (version_dir / "prompts" / "master_beater_user_FINAL.txt").read_text(
        encoding="utf-8"
    )
    assert "Target scene count: 6" in rendered_prompt
    assert "Break the story into exactly 6 scenes." in rendered_prompt


@pytest.mark.asyncio
async def test_style_model_forwarded(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        style_model="llama3.2:latest",
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        await pipeline.run()

    _, kwargs = mock_integrate.call_args
    assert kwargs.get("model") == "llama3.2:latest"


# ---------------------------------------------------------------------------
# Integration: graceful failure in script phase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_failure_does_not_crash_pipeline(tmp_path):
    """write_script failure is non-fatal: pipeline returns partial result with script=None."""
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=6,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", side_effect=ValueError("Continuity break")),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    assert result["script"] is None
    assert result["styled_script"] is None
    assert result["page_prompt"] is None
    assert result["errors"] == ["script: Continuity break"]
    assert isinstance(result["error_details"], list)
    assert len(cast(list[str], result["error_details"])) == 1
    assert "ValueError: Continuity break" in cast(list[str], result["error_details"])[0]
    mock_prompts.assert_not_called()
    assert result["raw_text"] is not None
    assert result["entities"] is not None
    assert result["story_bible"] is not None


@pytest.mark.asyncio
async def test_story_bible_failure_does_not_crash_pipeline(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=6,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", side_effect=ValueError("Beat coverage failed")),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    assert result["story_bible"] is None
    assert result["script"] is None
    assert result["styled_script"] is None
    assert result["page_prompt"] is None
    assert result["errors"] == ["story_bible: Beat coverage failed"]
    mock_prompts.assert_not_called()


@pytest.mark.asyncio
async def test_style_failure_does_not_crash_pipeline(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", side_effect=ValueError("Style rewrite failed")),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    assert result["script"] is not None
    assert result["styled_script"] is None
    assert result["page_prompt"] is None
    assert result["errors"] == ["style: Style rewrite failed"]
    mock_prompts.assert_not_called()


@pytest.mark.asyncio
async def test_partial_style_failure_records_error_and_continues_to_prompt(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
    )

    partial_error = StyleIntegrationPartialFailure(
        "Style integration left panels unchanged: [2]. Every panel must be visibly rewritten.",
        checkpoint=_STYLED_SCRIPT_CHECKPOINT,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", side_effect=partial_error),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    assert result["script"] is not None
    assert result["styled_script"] is not None
    assert result["page_prompt"] is not None
    assert result["errors"] == [
        "style: Style integration left panels unchanged: [2]. Every panel must be visibly rewritten."
    ]
    mock_prompts.assert_called_once()


@pytest.mark.asyncio
async def test_out_of_range_panel_count_records_error_but_continues(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=6,
    )

    out_of_range_script = _SCRIPT_CHECKPOINT.model_copy(
        update={
            "panel_count": 3,
            "generation_errors": [
                "Architecture alignment failed: expected 5 panels from story architecture, received 3. "
                "Accepting panel-count mismatch."
            ],
        }
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=out_of_range_script),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    assert result["script"] is not None
    assert cast(list[dict[str, object]], result["script"])[0]["panel_count"] == 3
    assert result["page_prompt"] is not None
    assert result["errors"] == [
        "script: Architecture alignment failed: expected 5 panels from story architecture, received 3. "
        "Accepting panel-count mismatch."
    ]
    mock_prompts.assert_called_once()
