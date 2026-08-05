"""Tests for the campaign-aware, versioned ComicPipeline."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import entities
from model_defaults import DEFAULT_MODEL
from prompter import DEFAULT_ART_DIRECTION_TEMPLATE_PATH
from prompt_templates import (
    DEFAULT_PROMPTS_DIR,
    PAGE_PROMPT_TEMPLATE_FILENAME,
    PROMPT_TEMPLATE_FILENAMES,
    STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME,
    STORY_ARCHITECT_USER_PROMPT_FILENAME,
    SCRIPTWRITER_SYSTEM_PROMPT_FILENAME,
    SCRIPTWRITER_USER_PROMPT_FILENAME,
    STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME,
    STYLE_INTEGRATOR_USER_PROMPT_FILENAME,
)
import scraper
import scriptwriter
import story_architect
from pipeline_events import PhaseWarning


@pytest.fixture(autouse=True)
def _patch_entities_continuity_merge(monkeypatch):
    def fake_merge(existing, incoming, model=DEFAULT_MODEL, **_kwargs):
        return existing.model_copy(deep=True), ["continuity fixture warning"]

    monkeypatch.setattr(entities, "_merge_entities_with_llm", fake_merge)
from style_integrator import StyleIntegrationPartialFailure
from pipeline import (
    WORKING_DIR_NAME,
    ComicPipeline,
    _create_version_dir,
    _ensure_working_dir,
    _lookup_episode,
    _next_version_name,
    _slugify,
    _working_dir,
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


def _single_panel_script_checkpoint(panel_index: int) -> scriptwriter.ScriptCheckpoint:
    panel = _SCRIPT_CHECKPOINT.pages[0].panels[panel_index - 1]
    return scriptwriter.ScriptCheckpoint(
        url=_SCRIPT_CHECKPOINT.url,
        title=_SCRIPT_CHECKPOINT.title,
        author=_SCRIPT_CHECKPOINT.author,
        model=_SCRIPT_CHECKPOINT.model,
        panel_count=1,
        total_pages=1,
        pages=[
            scriptwriter.Page(
                page_number=1,
                panel_count=1,
                panels=[panel],
            )
        ],
        generation_errors=[],
        scripted_at=_SCRIPT_CHECKPOINT.scripted_at,
    )


_STORY_BIBLE_CHECKPOINT = story_architect.StoryBibleCheckpoint(
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
    (version_dir / "02_5_entities_bible.json").write_text(
        _WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "02_5_episode_entities.json").write_text(
        _WORLD_CHECKPOINT.model_dump_json(), encoding="utf-8"
    )
    (version_dir / "02_5_story_bible.txt").write_text(
        _STORY_BIBLE_CHECKPOINT.story_bible + "\n", encoding="utf-8"
    )
    (version_dir / "02_6_story_bible_page_001.txt").write_text(
        _STORY_BIBLE_CHECKPOINT.story_bible + "\n", encoding="utf-8"
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
    _write_run_config(v001)

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


def _default_run_config() -> dict:
    return {
        "panel_count": 2,
        "total_pages": 1,
        "recap_version": "standard",
        "aspect_ratio": "3:2",
        "generation_mode": "page",
        "vignette": False,
        "skip_style": False,
        "generate_images": False,
        "rerun_from": None,
    }


def _write_run_config(version_dir: Path, config: dict | None = None) -> None:
    payload = {"run_config": config or _default_run_config()}
    (version_dir / "run_status.json").write_text(json.dumps(payload), encoding="utf-8")


def test_create_version_dir_first_run_no_clone(tmp_path):
    episode_dir = tmp_path / "episodes" / "ep1"
    episode_dir.mkdir(parents=True)

    version_dir, name, _ = _create_version_dir(episode_dir, rerun_from=None)

    assert name == "v001"
    assert version_dir.exists()
    assert list(version_dir.iterdir()) == []


def test_create_version_dir_clones_previous_version(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)
    _write_run_config(v001)

    version_dir, name, _ = _create_version_dir(
        episode_dir,
        rerun_from=None,
        new_config=_default_run_config(),
    )

    assert name == "v002"
    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_entities.json").exists()
    assert (version_dir / "02_5_story_bible.txt").exists()
    assert (version_dir / "03_script_page_001.json").exists()
    assert (version_dir / "03_5_styled_script_page_001.json").exists()
    assert (version_dir / "04_page_1_prompt.txt").exists()


def test_create_version_dir_rerun_from_prompt_deletes_only_prompt(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _, _ = _create_version_dir(episode_dir, rerun_from="prompt")

    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_entities.json").exists()
    assert (version_dir / "02_5_episode_entities.json").exists()
    assert (version_dir / "02_5_entities_bible.json").exists()
    assert (version_dir / "02_5_story_bible.txt").exists()
    assert (version_dir / "03_script_page_001.json").exists()
    assert (version_dir / "03_5_styled_script_page_001.json").exists()
    assert not (version_dir / "04_page_1_prompt.txt").exists()


def test_create_version_dir_rerun_from_architect_deletes_architect_onwards(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _, _ = _create_version_dir(episode_dir, rerun_from="architect")

    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_entities.json").exists()
    assert (version_dir / "02_5_episode_entities.json").exists()
    assert (version_dir / "02_5_entities_bible.json").exists()
    assert not (version_dir / "02_5_story_bible.txt").exists()
    assert not (version_dir / "03_script_page_001.json").exists()
    assert not (version_dir / "03_5_styled_script_page_001.json").exists()
    assert not (version_dir / "04_page_1_prompt.txt").exists()


def test_create_version_dir_rerun_from_entities_preserves_only_raw(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _, _ = _create_version_dir(episode_dir, rerun_from="entities")

    assert (version_dir / "01_raw_text.json").exists()
    assert not (version_dir / "02_entities.json").exists()
    assert not (version_dir / "02_5_episode_entities.json").exists()
    assert not (version_dir / "02_5_entities_bible.json").exists()
    assert not (version_dir / "02_5_story_bible.txt").exists()


def test_create_version_dir_rerun_from_scrape_deletes_all(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)

    version_dir, _, _ = _create_version_dir(episode_dir, rerun_from="scrape")

    assert not (version_dir / "01_raw_text.json").exists()
    assert not (version_dir / "02_entities.json").exists()
    assert not (version_dir / "02_5_episode_entities.json").exists()
    assert not (version_dir / "03_script_page_001.json").exists()
    assert not (version_dir / "03_5_styled_script_page_001.json").exists()
    assert not (version_dir / "04_page_1_prompt.txt").exists()


def test_create_version_dir_always_clones_creative_direction(tmp_path):
    """creative_direction.txt is episode guidance; clone regardless of rerun stage."""
    from pipeline import CREATIVE_DIRECTION_FILENAME

    episode_dir = tmp_path / "ep"
    working = episode_dir / WORKING_DIR_NAME
    working.mkdir(parents=True)
    _write_version_checkpoints(working)
    _write_run_config(working)
    guidance = "Prefer the tavern argument; avoid sword-leg close-ups.\n"
    (working / CREATIVE_DIRECTION_FILENAME).write_text(guidance, encoding="utf-8")

    stages = ["scrape", "entities", "architect", "script", "style", "prompt", None]
    for rerun_from in stages:
        version_dir, _, _ = _create_version_dir(
            episode_dir,
            rerun_from=rerun_from,
            new_config=_default_run_config(),
        )
        path = version_dir / CREATIVE_DIRECTION_FILENAME
        assert path.exists(), f"missing for rerun_from={rerun_from!r}"
        assert path.read_text(encoding="utf-8") == guidance


def test_create_version_dir_clones_from_working_not_latest_version(tmp_path):
    """Clone source is working/, even when a newer historical version diverged."""
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v002 = episode_dir / "v002"
    working = episode_dir / WORKING_DIR_NAME
    v001.mkdir(parents=True)
    v002.mkdir(parents=True)
    working.mkdir(parents=True)

    _write_version_checkpoints(v001)
    _write_version_checkpoints(v002)
    (v002 / "02_5_story_bible.txt").write_text("source: latest-version\n", encoding="utf-8")
    _write_version_checkpoints(working)
    (working / "02_5_story_bible.txt").write_text("source: working-edit\n", encoding="utf-8")
    _write_run_config(working)

    version_dir, name, _ = _create_version_dir(
        episode_dir,
        rerun_from="script",
        new_config=_default_run_config(),
    )

    assert name == "v003"
    bible = (version_dir / "02_5_story_bible.txt").read_text(encoding="utf-8")
    assert "working-edit" in bible
    assert not (version_dir / "03_script_page_001.json").exists()


def test_ensure_working_dir_seeds_from_latest_when_missing(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)
    _write_run_config(v001)

    working = _ensure_working_dir(episode_dir)

    assert working == _working_dir(episode_dir)
    assert (working / "01_raw_text.json").exists()
    assert (working / "02_5_story_bible.txt").exists()
    assert (working / "run_status.json").exists()


def test_create_version_dir_seeds_working_then_clones(tmp_path):
    """Migration path: only vNNN exists; create seeds working then clones from it."""
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)
    _write_run_config(v001)

    version_dir, name, _ = _create_version_dir(
        episode_dir,
        rerun_from=None,
        new_config=_default_run_config(),
    )

    working = _working_dir(episode_dir)
    assert working.exists()
    assert (working / "01_raw_text.json").exists()
    assert name == "v002"
    assert (version_dir / "01_raw_text.json").exists()
    assert (version_dir / "02_5_story_bible.txt").exists()


@pytest.mark.asyncio
async def test_first_run_writes_checkpoints_to_working_and_version(tmp_path):
    def _fake_integrate_style(*, output_path: Path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_STYLED_SCRIPT_CHECKPOINT.model_dump_json(), encoding="utf-8")
        return _STYLED_SCRIPT_CHECKPOINT

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
        patch("pipeline.integrate_style", side_effect=_fake_integrate_style),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    working = version_dir.parent / WORKING_DIR_NAME
    for name in (
        "01_raw_text.json",
        "02_entities.json",
        "02_5_story_bible.txt",
        "03_script_page_001.json",
        "03_5_styled_script_page_001.json",
        "04_page_1_prompt.txt",
    ):
        assert (version_dir / name).exists(), name
        assert (working / name).exists(), name


@pytest.mark.asyncio
async def test_run_keeps_prompt_audit_trail_out_of_working(tmp_path):
    """version/prompts is run audit only; campaign templates are the edit surface."""
    def _fake_integrate_style(*, output_path: Path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_STYLED_SCRIPT_CHECKPOINT.model_dump_json(), encoding="utf-8")
        return _STYLED_SCRIPT_CHECKPOINT

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
        patch("pipeline.integrate_style", side_effect=_fake_integrate_style),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    working = version_dir.parent / WORKING_DIR_NAME
    assert (version_dir / "prompts").is_dir()
    assert (version_dir / "prompts" / SCRIPTWRITER_SYSTEM_PROMPT_FILENAME).exists()
    assert not (working / "prompts").exists()
    # Phase-5 page prompt outputs are still episode checkpoints in working.
    assert (working / "04_page_1_prompt.txt").exists()


def test_create_version_dir_does_not_clone_prompts_audit_trail(tmp_path):
    """prompts/ is not a stage dependency; do not feed it forward into new versions."""
    episode_dir = tmp_path / "ep"
    working = episode_dir / WORKING_DIR_NAME
    working.mkdir(parents=True)
    _write_version_checkpoints(working)
    _write_run_config(working)
    stale = working / "prompts" / "scriptwriter_system_FINAL_page_001.txt"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("STALE AUDIT FROM WORKING", encoding="utf-8")
    (working / "04_page_1_prompt.txt").write_text("PRESERVED PAGE PROMPT", encoding="utf-8")

    version_dir, name, effective = _create_version_dir(
        episode_dir,
        rerun_from=None,
        new_config=_default_run_config(),
    )

    assert name == "v001"
    assert effective is None
    assert (version_dir / "04_page_1_prompt.txt").read_text(encoding="utf-8") == (
        "PRESERVED PAGE PROMPT"
    )
    assert not (version_dir / "prompts").exists()


def test_ensure_working_dir_seed_excludes_prompts_audit_trail(tmp_path):
    episode_dir = tmp_path / "ep"
    v001 = episode_dir / "v001"
    v001.mkdir(parents=True)
    _write_version_checkpoints(v001)
    _write_run_config(v001)
    (v001 / "prompts" / "scriptwriter_system.txt").write_text(
        "VERSION CAPTURE ONLY", encoding="utf-8"
    )

    working = _ensure_working_dir(episode_dir)

    assert (working / "01_raw_text.json").exists()
    assert (working / "04_page_1_prompt.txt").exists()
    assert not (working / "prompts").exists()


@pytest.mark.asyncio
async def test_manual_working_edit_is_cloned_into_next_version(tmp_path):
    episode_dir = _make_episode(
        tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing"
    )
    working = _ensure_working_dir(episode_dir)
    edited_bible = (working / "02_5_story_bible.txt").read_text(encoding="utf-8")
    edited_bible = edited_bible.replace(
        "Del the Druid raises her torch",
        "EDITED: Del the Druid raises her torch",
        1,
    )
    (working / "02_5_story_bible.txt").write_text(edited_bible, encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="script",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT) as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_not_called()
    mock_script.assert_called_once()

    version_dir = _version_dir_from_result(result)
    bible = (version_dir / "02_5_story_bible.txt").read_text(encoding="utf-8")
    assert "EDITED: Del the Druid raises her torch" in bible
    # Prior version history is untouched.
    original = (episode_dir / "v001" / "02_5_story_bible.txt").read_text(encoding="utf-8")
    assert "EDITED:" not in original
    assert "Del the Druid raises her torch" in original


@pytest.mark.asyncio
async def test_rerun_overwrites_working_only_for_recomputed_stages(tmp_path):
    episode_dir = _make_episode(
        tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing"
    )
    working = _ensure_working_dir(episode_dir)
    original_bible = (working / "02_5_story_bible.txt").read_text(encoding="utf-8")
    (working / "04_page_1_prompt.txt").write_text("OLD PROMPT", encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="prompt",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock),
        patch("pipeline.build_entities_from_raw"),
        patch("pipeline.create_story_bible"),
        patch("pipeline.write_script"),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value="NEW PROMPT"),
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    assert (working / "02_5_story_bible.txt").read_text(encoding="utf-8") == original_bible
    assert (version_dir / "04_page_1_prompt.txt").read_text(encoding="utf-8") == "NEW PROMPT"
    assert (working / "04_page_1_prompt.txt").read_text(encoding="utf-8") == "NEW PROMPT"


@pytest.mark.asyncio
async def test_manual_episode_entities_edit_survives_prompt_rerun(tmp_path):
    """Edits to working/02_5_episode_entities.json must not be clobbered on prompt-only rerun."""
    episode_dir = _make_episode(
        tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing"
    )
    working = _ensure_working_dir(episode_dir)
    edited = _WORLD_CHECKPOINT.model_copy(deep=True)
    edited.player_characters[0].description = "EDITED: Del in glowing mossy robes"
    edited_json = edited.model_dump_json(indent=2)
    (working / "02_5_episode_entities.json").write_text(edited_json, encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="prompt",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock),
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.write_entities_bible") as mock_bible,
        patch("pipeline.write_episode_entities") as mock_episode_entities,
        patch("pipeline.create_story_bible"),
        patch("pipeline.write_script"),
        patch("pipeline.integrate_style"),
        patch("pipeline.prepare_page_prompt_template", return_value="NEW PROMPT") as mock_prompt,
    ):
        result = await pipeline.run()

    mock_entities.assert_not_called()
    mock_bible.assert_not_called()
    mock_episode_entities.assert_not_called()
    mock_prompt.assert_called_once()
    _, prompt_kwargs = mock_prompt.call_args
    assert prompt_kwargs["world"].player_characters[0].description == (
        "EDITED: Del in glowing mossy robes"
    )

    version_dir = _version_dir_from_result(result)
    for path in (
        version_dir / "02_5_episode_entities.json",
        working / "02_5_episode_entities.json",
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["player_characters"][0]["description"] == (
            "EDITED: Del in glowing mossy robes"
        )


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

    version_dir = _version_dir_from_result(result)
    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs["entities_checkpoint_path"] == version_dir / "02_5_episode_entities.json"
    _, script_kwargs = mock_script.call_args
    assert script_kwargs["entities_checkpoint_path"] == version_dir / "02_5_episode_entities.json"

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
async def test_first_run_writes_entities_bible_artifacts(tmp_path):
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

    version_dir = _version_dir_from_result(result)
    campaign_bible = tmp_path / "dreadmarsh" / "entities_bible.json"
    version_copy = version_dir / "02_5_entities_bible.json"
    episode_entities = version_dir / "02_5_episode_entities.json"

    assert campaign_bible.exists()
    assert version_copy.exists()
    assert episode_entities.exists()
    assert json.loads(campaign_bible.read_text(encoding="utf-8"))["player_characters"][0]["name"] == "Del"
    assert json.loads(episode_entities.read_text(encoding="utf-8"))["player_characters"][0]["name"] == "Del"


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

    # Continuity merge FINALs are written even when the LLM merge is faked.
    assert (
        version_dir / "prompts" / "entities_continuity_system_FINAL.txt"
    ).exists()
    assert (version_dir / "prompts" / "entities_continuity_user_FINAL.txt").exists()
    continuity_user = (
        version_dir / "prompts" / "entities_continuity_user_FINAL.txt"
    ).read_text(encoding="utf-8")
    assert "Existing entities bible:" in continuity_user
    assert "Current episode entities:" in continuity_user

    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "story_architect_system_FINAL.txt"
    ).read_text(encoding="utf-8")
    assert architect_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "story_architect_user_FINAL.txt"
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
        story_architect_system_prompt=architect_system,
        story_architect_user_prompt=architect_user,
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
    assert (prompts_dir / STORY_ARCHITECT_SYSTEM_PROMPT_FILENAME).read_text(encoding="utf-8") == "ARCHITECT SYSTEM OVERRIDE"
    assert (prompts_dir / STORY_ARCHITECT_USER_PROMPT_FILENAME).read_text(encoding="utf-8") == "ARCHITECT USER OVERRIDE"
    assert (prompts_dir / SCRIPTWRITER_SYSTEM_PROMPT_FILENAME).read_text(encoding="utf-8") == "SYSTEM OVERRIDE"
    assert (prompts_dir / SCRIPTWRITER_USER_PROMPT_FILENAME).read_text(encoding="utf-8") == "USER OVERRIDE"
    assert (prompts_dir / STYLE_INTEGRATOR_SYSTEM_PROMPT_FILENAME).read_text(encoding="utf-8") == "STYLE SYSTEM OVERRIDE"
    assert (prompts_dir / STYLE_INTEGRATOR_USER_PROMPT_FILENAME).read_text(encoding="utf-8") == "STYLE USER OVERRIDE"
    assert (prompts_dir / PAGE_PROMPT_TEMPLATE_FILENAME).read_text(encoding="utf-8") == "CUSTOM PAGE PROMPT: {panel_count}"

    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs["system_prompt_text"] == (
        version_dir / "prompts" / "story_architect_system_FINAL.txt"
    ).read_text(encoding="utf-8")
    assert architect_kwargs["user_prompt_text"] == (
        version_dir / "prompts" / "story_architect_user_FINAL.txt"
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
async def test_panel_generation_mode_writes_one_prompt_per_panel(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        total_pages=1,
        generation_mode="panel",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch(
            "pipeline.write_script",
            side_effect=[_single_panel_script_checkpoint(1), _single_panel_script_checkpoint(2)],
        ),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", side_effect=["PANEL 1", "PANEL 2"]) as mock_prompts,
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)

    assert (version_dir / "04_page_1_panel_1_prompt.txt").exists()
    assert (version_dir / "04_page_1_panel_2_prompt.txt").exists()
    assert mock_prompts.call_count == 2
    assert (version_dir / "04_page_1_panel_1_prompt.txt").read_text(encoding="utf-8") == "PANEL 1"
    assert (version_dir / "04_page_1_panel_2_prompt.txt").read_text(encoding="utf-8") == "PANEL 2"


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
    """Recap switch uses cached variants: no scrape/entities rebuild; architect onward runs."""
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
    mock_entities.assert_not_called()
    mock_architect.assert_called_once()
    mock_script.assert_called_once()
    mock_prompts.assert_called_once()

    version_dir = _version_dir_from_result(result)
    raw_path = version_dir / "01_raw_text.json"
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw_payload["selected_recap"] == "short"
    assert raw_payload["content"] == _RAW_CHECKPOINT.recap_variants["short"]
    assert (version_dir / "02_entities.json").exists()


@pytest.mark.asyncio
async def test_cached_raw_missing_recap_variant_hard_errors_without_scraping(tmp_path):
    episode_dir = _make_episode(
        tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing"
    )
    # Leave only the standard variant in the prior scrape.
    raw = scraper.RawTextCheckpoint.model_validate_json(
        (episode_dir / "v001" / "01_raw_text.json").read_text(encoding="utf-8")
    )
    raw = raw.model_copy(
        update={
            "recap_variants": {"standard": raw.recap_variants["standard"]},
            "selected_recap": "standard",
            "content": raw.recap_variants["standard"],
        }
    )
    (episode_dir / "v001" / "01_raw_text.json").write_text(
        raw.model_dump_json(), encoding="utf-8"
    )

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        recap_version="long",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
    ):
        with pytest.raises(ValueError, match="Recap variant 'long' is not available"):
            await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_not_called()


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
        rerun_from="architect",
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
async def test_stop_after_entities_reruns_entities_only(tmp_path):
    """rerun_from=entities + stop_after=entities: refresh entities, do not continue."""
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    events: list[object] = []
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="entities",
        stop_after="entities",
        event_callback=events.append,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT) as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.integrate_style") as mock_integrate,
        patch("pipeline.prepare_page_prompt_template") as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_called_once()
    mock_architect.assert_not_called()
    mock_script.assert_not_called()
    mock_integrate.assert_not_called()
    mock_prompts.assert_not_called()
    assert result["version"] == "v002"
    assert result["entities"] is not None
    assert result["story_bible"] is None
    assert result["script"] is None
    assert result["errors"] == []

    version_dir = _version_dir_from_result(result)
    assert (version_dir / "02_entities.json").exists()
    assert not (version_dir / "02_5_story_bible.txt").exists()
    assert not list(version_dir.glob("03_script_page_*.json"))

    from pipeline_events import RunCompleted

    completed = [e for e in events if isinstance(e, RunCompleted)]
    assert len(completed) == 1
    assert completed[0].status == "ok"
    assert completed[0].failed_phases == []
    assert "entities" in completed[0].checkpoints
    assert "story_bible" not in completed[0].checkpoints


@pytest.mark.asyncio
async def test_stop_after_style_reruns_style_not_prompt(tmp_path):
    """rerun_from=style + stop_after=style: style runs, prompt does not."""
    _make_episode(tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="style",
        stop_after="style",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock) as mock_scrape,
        patch("pipeline.build_entities_from_raw") as mock_entities,
        patch("pipeline.create_story_bible") as mock_architect,
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT) as mock_integrate,
        patch("pipeline.prepare_page_prompt_template") as mock_prompts,
    ):
        result = await pipeline.run()

    mock_scrape.assert_not_awaited()
    mock_entities.assert_not_called()
    mock_architect.assert_not_called()
    mock_script.assert_not_called()
    mock_integrate.assert_called_once()
    mock_prompts.assert_not_called()
    assert result["version"] == "v002"
    assert result["styled_script"] is not None
    assert result["page_prompt"] is None

    version_dir = _version_dir_from_result(result)
    # Prompts are invalidated by rerun_from=style and must not be regenerated.
    assert not list(version_dir.glob("04_page_*_prompt.txt"))


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
            patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
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
    """Campaign art_direction/<style>.json is used when present."""
    template_path = tmp_path / "dreadmarsh" / "art_direction" / "custom.json"
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
    assert result.get("run_config", {}).get("art_style") == "campaign:custom" or (
        pipeline.art_style == "campaign:custom"
    )


@pytest.mark.asyncio
async def test_bundled_default_art_style_used_when_campaign_has_none(tmp_path):
    """First run uses bundled default style without creating a campaign art file."""
    campaign_template = tmp_path / "dreadmarsh" / "art_direction_template.json"
    campaign_art_dir = tmp_path / "dreadmarsh" / "art_direction"

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

    assert not campaign_template.exists()
    assert not campaign_art_dir.exists() or not any(campaign_art_dir.glob("*.json"))

    _, kwargs = mock_integrate.call_args
    version_template_path = _version_dir_from_result(result) / "art_direction_template.json"
    assert kwargs["art_style_template_path"] == version_template_path
    assert version_template_path.read_text(encoding="utf-8") == (
        DEFAULT_ART_DIRECTION_TEMPLATE_PATH.read_text(encoding="utf-8")
    )
    assert pipeline.art_style == "bundled:brutalist"


@pytest.mark.asyncio
async def test_art_style_id_selects_named_style(tmp_path):
    """art_style id selects a specific campaign style over the campaign default order."""
    art_dir = tmp_path / "dreadmarsh" / "art_direction"
    first = art_dir / "aaa.json"
    selected = art_dir / "zzz.json"
    art_dir.mkdir(parents=True, exist_ok=True)
    first.write_text(
        '{"base_style": "First style.", "characters": "Cast A.", '
        '"color_palette": "BW.", "layout_and_composition": "Single page.", '
        '"lettering_and_dialog": "Hand lettering.", '
        '"text_rendering_guide": "Balloons and boxes."}',
        encoding="utf-8",
    )
    selected.write_text(
        '{"base_style": "Selected style.", "characters": "Cast Z.", '
        '"color_palette": "Color.", "layout_and_composition": "Single page.", '
        '"lettering_and_dialog": "Sharp lettering.", '
        '"text_rendering_guide": "Distinct balloons and boxes."}',
        encoding="utf-8",
    )

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        art_style="campaign:zzz",
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

    version_template_path = _version_dir_from_result(result) / "art_direction_template.json"
    _, kwargs = mock_integrate.call_args
    assert kwargs["art_style_template_path"] == version_template_path
    assert version_template_path.read_text(encoding="utf-8") == selected.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_explicit_art_template_overrides_campaign_default(tmp_path):
    """Explicit art_style_template constructor arg takes precedence over campaign default."""
    campaign_template = tmp_path / "dreadmarsh" / "art_direction" / "campaign.json"
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
        architect_model="llama3.1:8b",
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
    assert kwargs.get("story_bible_checkpoint_path").name == "02_6_story_bible_page_001.txt"


@pytest.mark.asyncio
async def test_architect_prompt_uses_total_scene_count(tmp_path):
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
    rendered_prompt = (version_dir / "prompts" / "story_architect_user_FINAL.txt").read_text(
        encoding="utf-8"
    )
    assert "Target scene count: 6" in rendered_prompt
    assert "Break the story into exactly 6 scenes." in rendered_prompt


@pytest.mark.asyncio
async def test_architect_prompt_includes_creative_direction_from_working(tmp_path):
    from pipeline import CREATIVE_DIRECTION_FILENAME

    episode_dir = _make_episode(
        tmp_path, "dreadmarsh", "https://example.test/story", "Dreadmarsh Crossing"
    )
    working = _ensure_working_dir(episode_dir)
    guidance = "Prefer the tavern argument; avoid any sword-for-a-leg close-up."
    (working / CREATIVE_DIRECTION_FILENAME).write_text(guidance + "\n", encoding="utf-8")

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        rerun_from="architect",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock),
        patch("pipeline.build_entities_from_raw"),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    assert (version_dir / CREATIVE_DIRECTION_FILENAME).exists()
    assert guidance in (version_dir / CREATIVE_DIRECTION_FILENAME).read_text(encoding="utf-8")

    _, architect_kwargs = mock_architect.call_args
    user_prompt = architect_kwargs["user_prompt_text"]
    assert user_prompt.startswith("**Creative direction (user):**")
    assert guidance in user_prompt
    final_user = (version_dir / "prompts" / "story_architect_user_FINAL.txt").read_text(
        encoding="utf-8"
    )
    assert guidance in final_user

    # Scriptwriter is not wired to creative_direction in this pass.
    script_final = (
        version_dir / "prompts" / "scriptwriter_user_FINAL_page_001.txt"
    ).read_text(encoding="utf-8")
    assert "Creative direction" not in script_final


@pytest.mark.asyncio
async def test_architect_prompt_omits_creative_direction_when_file_missing(tmp_path):
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
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    _, architect_kwargs = mock_architect.call_args
    assert "Creative direction" not in architect_kwargs["user_prompt_text"]

    version_dir = _version_dir_from_result(result)
    final_user = (version_dir / "prompts" / "story_architect_user_FINAL.txt").read_text(
        encoding="utf-8"
    )
    assert "Creative direction" not in final_user


@pytest.mark.asyncio
async def test_vignette_uses_vignette_architect_templates_and_page_prompts(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        total_pages=1,
        vignette=True,
        generation_mode="page",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT) as mock_architect,
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT) as mock_prompts,
    ):
        result = await pipeline.run()

    _, architect_kwargs = mock_architect.call_args
    assert architect_kwargs.get("scene_count") == 2
    assert result.get("run_config", {}).get("vignette") is True

    version_dir = _version_dir_from_result(result)
    prompts_dir = version_dir / "prompts"
    rendered_system = (
        prompts_dir / "story_architect_vignette_system_FINAL.txt"
    ).read_text(encoding="utf-8")
    rendered_user = (
        prompts_dir / "story_architect_vignette_user_FINAL.txt"
    ).read_text(encoding="utf-8")
    assert "single tight moment" in rendered_system.casefold() or "one tight" in rendered_system.casefold()
    assert "vignette" in rendered_system.casefold() or "one continuous moment" in rendered_system.casefold()
    assert "Target scene count: 2" in rendered_user
    # Active vignette FINALs use vignette names; standard story_architect_*_FINAL is not written.
    assert not (prompts_dir / "story_architect_system_FINAL.txt").exists()
    assert not (prompts_dir / "story_architect_user_FINAL.txt").exists()
    # Standard templates are still captured for audit of available campaign files.
    assert (prompts_dir / "story_architect_system.txt").exists()
    assert (prompts_dir / "story_architect_vignette_system.txt").exists()

    assert (version_dir / "04_page_1_prompt.txt").exists()
    assert not list(version_dir.glob("04_page_*_panel_*_prompt.txt"))
    assert mock_prompts.call_count == 1
    _, prompt_kwargs = mock_prompts.call_args
    assert prompt_kwargs.get("generation_mode") == "page"


@pytest.mark.asyncio
async def test_vignette_with_panel_mode_writes_one_prompt_per_panel(tmp_path):
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        total_pages=1,
        vignette=True,
        generation_mode="panel",
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch(
            "pipeline.write_script",
            side_effect=[_single_panel_script_checkpoint(1), _single_panel_script_checkpoint(2)],
        ),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", side_effect=["PANEL 1", "PANEL 2"]) as mock_prompts,
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    assert (version_dir / "04_page_1_panel_1_prompt.txt").exists()
    assert (version_dir / "04_page_1_panel_2_prompt.txt").exists()
    assert mock_prompts.call_count == 2


@pytest.mark.asyncio
async def test_image_generation_stage_runs_when_enabled(tmp_path):
    fake_generator = MagicMock()
    fake_generator.generate_image.return_value = b"png-bytes"
    fake_generator.save_image.side_effect = (
        lambda image_bytes, output_path: Path(output_path).write_bytes(image_bytes) or Path(output_path)
    )

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        total_pages=1,
        generate_images=True,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
        patch("pipeline.ImageGenerator", return_value=fake_generator) as mock_image_generator,
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    assert (version_dir / "05_page_1.png").exists()
    fake_generator.generate_image.assert_called_once_with(_PAGE_PROMPT)
    mock_image_generator.assert_called_once_with(model="gemini-2.5-flash-image")


@pytest.mark.asyncio
async def test_panel_image_generation_stitches_final_page(tmp_path):
    fake_generator = MagicMock()
    fake_generator.generate_image.return_value = b"png-bytes"
    fake_generator.save_image.side_effect = (
        lambda image_bytes, output_path: Path(output_path).write_bytes(image_bytes) or Path(output_path)
    )

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        total_pages=1,
        generation_mode="panel",
        generate_images=True,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch(
            "pipeline.write_script",
            side_effect=[_single_panel_script_checkpoint(1), _single_panel_script_checkpoint(2)],
        ),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
        patch("pipeline.ImageGenerator", return_value=fake_generator),
        patch("pipeline.stitch_panel_images", return_value=Path("stitched.png")) as mock_stitch,
    ):
        result = await pipeline.run()

    version_dir = _version_dir_from_result(result)
    assert (version_dir / "05_page_1_panel_1.png").exists()
    assert (version_dir / "05_page_1_panel_2.png").exists()
    mock_stitch.assert_called_once()
    assert mock_stitch.call_args.args[1] == version_dir / "06_page_1.png"


@pytest.mark.asyncio
async def test_image_generation_stage_is_skipped_by_default(tmp_path):
    fake_generator = MagicMock()

    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        total_pages=1,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script", return_value=_SCRIPT_CHECKPOINT),
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
        patch("pipeline.ImageGenerator", return_value=fake_generator) as mock_image_generator,
    ):
        await pipeline.run()

    mock_image_generator.assert_not_called()
    fake_generator.generate_image.assert_not_called()


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
async def test_script_prompt_variable_mismatch_reports_template_file(tmp_path):
    bad_scriptwriter_user = tmp_path / "bad_scriptwriter_user.txt"
    bad_scriptwriter_user.write_text(
        "Story architecture:\n{story_bible}\n",
        encoding="utf-8",
    )

    emitted_events = []
    pipeline = ComicPipeline(
        url="https://example.test/story",
        campaign="dreadmarsh",
        campaigns_root=tmp_path,
        panel_count=2,
        scriptwriter_user_prompt=bad_scriptwriter_user,
        event_callback=emitted_events.append,
    )

    with (
        patch("pipeline.scrape_scrybequill", new_callable=AsyncMock, return_value=_RAW_CHECKPOINT),
        patch("pipeline.build_entities_from_raw", return_value=_WORLD_CHECKPOINT),
        patch("pipeline.create_story_bible", return_value=_STORY_BIBLE_CHECKPOINT),
        patch("pipeline.write_script") as mock_script,
        patch("pipeline.integrate_style", return_value=_STYLED_SCRIPT_CHECKPOINT),
        patch("pipeline.prepare_page_prompt_template", return_value=_PAGE_PROMPT),
    ):
        result = await pipeline.run()

    assert result["script"] is None
    assert result["page_prompt"] is None
    assert result["errors"]
    first_error = cast(list[str], result["errors"])[0]
    assert first_error.startswith("script: Prompt template variable mismatch in ")
    assert "/prompts/scriptwriter_user.txt" in first_error
    assert "{story_bible}" in first_error

    details = cast(list[str], result["error_details"])
    assert details
    assert "KeyError: 'story_bible'" in details[0]
    assert mock_script.call_count == 0

    prompt_warning_events = [
        event
        for event in emitted_events
        if isinstance(event, PhaseWarning) and event.message == "Prompt template update required"
    ]
    assert prompt_warning_events
    assert "/prompts/scriptwriter_user.txt" in prompt_warning_events[-1].warning


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
