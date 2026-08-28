from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from character_refs import (
    character_filename,
    scan_character_library,
    select_character_reference_images,
)
from entities import Character, WorldStateCheckpoint
from scriptwriter import Page, Panel, ScriptCheckpoint


def _panel(
    *,
    index: int = 1,
    page_number: int = 1,
    visual_action: str,
    characters: list[str] | None = None,
    setting: str = "A hall",
) -> Panel:
    return Panel(
        index=index,
        page_number=page_number,
        panel_scale="medium",
        panel_shape="standard",
        setting=setting,
        visual_action=visual_action,
        characters=list(characters or []),
    )


def _world(*characters: Character, npcs: list[Character] | None = None) -> WorldStateCheckpoint:
    return WorldStateCheckpoint(
        url="https://example.test/story",
        model="test",
        player_characters=list(characters),
        npcs=list(npcs or []),
        locations=[],
        beats=[],
        analyzed_at="2026-01-01T00:00:00+00:00",
    )


def _script(*panels: Panel, page_number: int = 1) -> ScriptCheckpoint:
    return ScriptCheckpoint(
        url="https://example.test/story",
        model="test",
        panel_count=len(panels),
        total_pages=1,
        pages=[
            Page(
                page_number=page_number,
                panel_count=len(panels),
                panels=list(panels),
            )
        ],
        scripted_at="2026-01-01T00:00:00+00:00",
    )


def _write_library(campaign_root: Path, *names: str) -> dict[str, Path]:
    folder = campaign_root / "characters"
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name in names:
        path = folder / character_filename(name)
        path.write_bytes(f"png:{name}".encode())
        paths[name] = path
    return paths


def _write_version(
    version_dir: Path,
    *,
    world: WorldStateCheckpoint,
    script: ScriptCheckpoint,
    styled: bool = False,
) -> None:
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "02_5_episode_entities.json").write_text(
        json.dumps(world.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    page_number = script.pages[0].page_number
    filename = (
        f"03_5_styled_script_page_{page_number:03d}.json"
        if styled
        else f"03_script_page_{page_number:03d}.json"
    )
    (version_dir / filename).write_text(
        json.dumps(script.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )


def test_character_filename_replaces_spaces_with_underscores() -> None:
    assert character_filename("Tharivol") == "Tharivol.png"
    assert character_filename("Maisie Fae") == "Maisie_Fae.png"


def test_scan_character_library_reads_png_files_and_ignores_junk(tmp_path: Path) -> None:
    campaign_root = tmp_path / "dreadmarsh"
    folder = campaign_root / "characters"
    folder.mkdir(parents=True)
    (folder / "Tharivol.png").write_bytes(b"thar")
    (folder / "Maisie_Fae.png").write_bytes(b"maisie")
    (folder / "notes.txt").write_text("ignore", encoding="utf-8")
    (folder / "Orion.jpg").write_bytes(b"jpeg")
    (folder / "Vendetta").mkdir()
    (folder / "Vendetta" / "01_front.png").write_bytes(b"nested")

    library = scan_character_library(campaign_root)

    assert set(library) == {"tharivol", "maisie_fae"}
    assert library["tharivol"] == folder / "Tharivol.png"
    assert library["maisie_fae"] == folder / "Maisie_Fae.png"


def test_scan_character_library_missing_folder_is_empty(tmp_path: Path) -> None:
    assert scan_character_library(tmp_path / "dreadmarsh") == {}


def test_select_matches_name_and_alias_filenames(tmp_path: Path) -> None:
    campaign_root = tmp_path / "dreadmarsh"
    version_dir = campaign_root / "ep" / "v001"
    _write_library(campaign_root, "Maisie Faye")
    _write_version(
        version_dir,
        world=_world(
            Character(
                name="Maisie Fae",
                description="A seer",
                aliases=["Maisie Faye"],
            )
        ),
        script=_script(_panel(visual_action="Maisie Fae holds the ticket.")),
    )

    selected = select_character_reference_images(
        campaign_root=campaign_root,
        version_dir=version_dir,
        page_number=1,
        panel_number=None,
        model="gemini-2.5-flash-image",
    )

    assert [ref.slug for ref in selected] == ["Maisie_Faye"]
    assert selected[0].name == "Maisie Fae"
    assert selected[0].path == campaign_root / "characters" / "Maisie_Faye.png"


def test_select_ranks_by_mention_count_and_truncates_to_model_cap(tmp_path: Path) -> None:
    campaign_root = tmp_path / "dreadmarsh"
    version_dir = campaign_root / "ep" / "v001"
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    _write_library(campaign_root, *names)
    _write_version(
        version_dir,
        world=_world(
            *[Character(name=name, description=f"{name} desc") for name in names]
        ),
        script=_script(
            _panel(
                visual_action=(
                    "Echo Echo Echo Echo Echo. "
                    "Delta Delta Delta Delta. "
                    "Charlie Charlie Charlie. "
                    "Bravo Bravo. "
                    "Alpha."
                ),
                characters=names,
            )
        ),
    )

    selected = select_character_reference_images(
        campaign_root=campaign_root,
        version_dir=version_dir,
        page_number=1,
        panel_number=None,
        model="gemini-2.5-flash-image",
    )

    assert [ref.name for ref in selected] == ["Echo", "Delta", "Charlie"]


def test_select_prefers_player_characters_on_a_tie(tmp_path: Path) -> None:
    campaign_root = tmp_path / "dreadmarsh"
    version_dir = campaign_root / "ep" / "v001"
    _write_library(campaign_root, "Witch", "Del")
    _write_version(
        version_dir,
        world=_world(
            Character(name="Del", description="A druid"),
            npcs=[Character(name="Witch", description="A hag")],
        ),
        script=_script(
            _panel(visual_action="Del faces the Witch.", characters=["Del", "Witch"])
        ),
    )

    selected = select_character_reference_images(
        campaign_root=campaign_root,
        version_dir=version_dir,
        page_number=1,
        panel_number=None,
        model="gemini-2.5-flash-image",
    )

    assert [ref.name for ref in selected] == ["Del", "Witch"]


def test_select_panel_mode_uses_only_that_panel(tmp_path: Path) -> None:
    campaign_root = tmp_path / "dreadmarsh"
    version_dir = campaign_root / "ep" / "v001"
    _write_library(campaign_root, "Del", "Maisie Fae")
    _write_version(
        version_dir,
        world=_world(
            Character(name="Del", description="A druid"),
            Character(name="Maisie Fae", description="A seer"),
        ),
        script=_script(
            _panel(index=1, visual_action="Del raises a torch.", characters=["Del"]),
            _panel(
                index=2,
                visual_action="Maisie Fae holds the ticket.",
                characters=["Maisie Fae"],
            ),
        ),
    )

    selected = select_character_reference_images(
        campaign_root=campaign_root,
        version_dir=version_dir,
        page_number=1,
        panel_number=1,
        model="gemini-2.5-flash-image",
    )

    assert [ref.name for ref in selected] == ["Del"]


def test_select_prefers_styled_script_when_present(tmp_path: Path) -> None:
    campaign_root = tmp_path / "dreadmarsh"
    version_dir = campaign_root / "ep" / "v001"
    _write_library(campaign_root, "Del", "Orion")
    _write_version(
        version_dir,
        world=_world(
            Character(name="Del", description="A druid"),
            Character(name="Orion", description="A ranger"),
        ),
        script=_script(_panel(visual_action="Del stands alone.", characters=["Del"])),
    )
    _write_version(
        version_dir,
        world=_world(
            Character(name="Del", description="A druid"),
            Character(name="Orion", description="A ranger"),
        ),
        script=_script(
            _panel(visual_action="Orion marks the tracks.", characters=["Orion"])
        ),
        styled=True,
    )

    selected = select_character_reference_images(
        campaign_root=campaign_root,
        version_dir=version_dir,
        page_number=1,
        panel_number=None,
        model="gemini-2.5-flash-image",
    )

    assert [ref.name for ref in selected] == ["Orion"]


def test_select_returns_empty_when_folder_or_script_missing(tmp_path: Path) -> None:
    campaign_root = tmp_path / "dreadmarsh"
    version_dir = campaign_root / "ep" / "v001"
    version_dir.mkdir(parents=True)

    assert (
        select_character_reference_images(
            campaign_root=campaign_root,
            version_dir=version_dir,
            page_number=1,
            panel_number=None,
            model="gemini-2.5-flash-image",
        )
        == []
    )

    _write_library(campaign_root, "Del")
    assert (
        select_character_reference_images(
            campaign_root=campaign_root,
            version_dir=version_dir,
            page_number=1,
            panel_number=None,
            model="gemini-2.5-flash-image",
        )
        == []
    )
    assert (
        select_character_reference_images(
            campaign_root=None,
            version_dir=version_dir,
            page_number=1,
            panel_number=None,
            model="gemini-2.5-flash-image",
        )
        == []
    )
