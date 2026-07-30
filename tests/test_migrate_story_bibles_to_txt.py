from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))

import migrate_story_bibles_to_txt as migrate


def _write_json_bible(path: Path, story_bible: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "url": "https://example.test/story",
                "title": "Test",
                "author": "GM",
                "model": "test-model",
                "scene_count": 1,
                "story_bible": story_bible,
                "generation_errors": [],
                "created_at": "2026-05-04T00:00:00+00:00",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_migrate_dry_run_does_not_write(tmp_path: Path) -> None:
    json_path = tmp_path / "v001" / "02_5_story_bible.json"
    _write_json_bible(json_path, "Scene 1:\nHello marsh.\n\nScene 2:\nMore mud.")
    original = json_path.read_text(encoding="utf-8")

    status = migrate.migrate_file(json_path, write=False)

    assert status == "would-write"
    assert not json_path.with_suffix(".txt").exists()
    assert json_path.read_text(encoding="utf-8") == original


def test_migrate_write_creates_parallel_txt_and_leaves_json(tmp_path: Path) -> None:
    json_path = tmp_path / "working" / "02_5_story_bible.json"
    body = 'Scene 1:\nDel says "stay close."'
    _write_json_bible(json_path, body)
    original = json_path.read_text(encoding="utf-8")

    status = migrate.migrate_file(json_path, write=True)

    txt_path = json_path.with_suffix(".txt")
    assert status == "wrote"
    assert txt_path.exists()
    on_disk = txt_path.read_text(encoding="utf-8")
    assert on_disk.startswith("Scene 1:")
    assert 'Del says "stay close."' in on_disk
    assert r"\n" not in on_disk
    assert json_path.read_text(encoding="utf-8") == original


def test_migrate_skips_when_txt_already_exists(tmp_path: Path) -> None:
    json_path = tmp_path / "02_6_story_bible_page_001.json"
    _write_json_bible(json_path, "Scene 1:\nPage slice.")
    txt_path = json_path.with_suffix(".txt")
    txt_path.write_text("already migrated\n", encoding="utf-8")

    status = migrate.migrate_file(json_path, write=True)

    assert status == "skip-exists"
    assert txt_path.read_text(encoding="utf-8") == "already migrated\n"


def test_migrate_main_discovers_nested_files(tmp_path: Path) -> None:
    full = tmp_path / "camp" / "ep" / "v001" / "02_5_story_bible.json"
    page = tmp_path / "camp" / "ep" / "v001" / "02_6_story_bible_page_001.json"
    panel = tmp_path / "camp" / "ep" / "v001" / "02_6_story_bible_page_001_panel_002.json"
    _write_json_bible(full, "Scene 1:\nFull.")
    _write_json_bible(page, "Scene 1:\nPage.")
    _write_json_bible(panel, "Scene 2:\nPanel.")

    rc = migrate.main(["--campaigns-root", str(tmp_path), "--write"])

    assert rc == 0
    assert full.with_suffix(".txt").exists()
    assert page.with_suffix(".txt").exists()
    assert panel.with_suffix(".txt").exists()


def test_migrate_main_accepts_positional_folder_tree(tmp_path: Path) -> None:
    target = tmp_path / "ep" / "v003"
    other = tmp_path / "ep" / "v004"
    _write_json_bible(target / "02_5_story_bible.json", "Scene 1:\nOnly this tree.")
    _write_json_bible(other / "02_5_story_bible.json", "Scene 1:\nLeave alone.")

    rc = migrate.main([str(target), "--write"])

    assert rc == 0
    assert (target / "02_5_story_bible.txt").exists()
    assert not (other / "02_5_story_bible.txt").exists()


def test_migrate_main_accepts_single_json_file(tmp_path: Path) -> None:
    json_path = tmp_path / "v001" / "02_5_story_bible.json"
    sibling = tmp_path / "v001" / "02_6_story_bible_page_001.json"
    _write_json_bible(json_path, "Scene 1:\nOne file.")
    _write_json_bible(sibling, "Scene 1:\nSibling.")

    rc = migrate.main([str(json_path), "--write"])

    assert rc == 0
    assert json_path.with_suffix(".txt").exists()
    assert not sibling.with_suffix(".txt").exists()


def test_migrate_default_root_is_app_data_campaigns(monkeypatch, tmp_path: Path) -> None:
    """No args should scan app_paths.default_campaigns_root(), not repo/campaigns."""
    app_campaigns = tmp_path / "app-data-campaigns"
    bible = app_campaigns / "flail" / "ep" / "v001" / "02_5_story_bible.json"
    _write_json_bible(bible, "Scene 1:\nFrom app data.")

    monkeypatch.setattr(migrate, "default_campaigns_root", lambda: app_campaigns)

    rc = migrate.main(["--write"])

    assert rc == 0
    assert bible.with_suffix(".txt").exists()
