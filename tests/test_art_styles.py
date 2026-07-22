"""Tests for art style discovery and resolution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from art_styles import (
    DEFAULT_ART_STYLE_STEM,
    ArtStyleOption,
    campaign_art_direction_dir,
    default_art_style,
    list_art_styles,
    resolve_art_style,
)


def _write_style(path: Path, base_style: str = "Test style.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "base_style": base_style,
                "characters": "Consistent cast.",
                "color_palette": "Black and white.",
                "layout_and_composition": "Vertical layout.",
                "lettering_and_dialog": "Hand lettering.",
                "text_rendering_guide": "Dialogue balloons and caption boxes.",
            }
        ),
        encoding="utf-8",
    )


def test_list_art_styles_includes_bundled_and_campaign(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _write_style(bundled / "brutalist.json", "Brutalist.")
    _write_style(bundled / "doom-metal.json", "Doom metal.")
    _write_style(bundled / "_blank.json", "Should be skipped.")

    campaigns_root = tmp_path / "campaigns"
    camp_dir = campaign_art_direction_dir(campaigns_root, "flail")
    _write_style(camp_dir / "brutalist.json", "Campaign brutalist override.")
    _write_style(camp_dir / "custom.json", "Campaign only.")

    monkeypatch.setattr("art_styles.bundled_art_direction_dir", lambda: bundled)

    styles = list_art_styles(campaigns_root, "flail")
    ids = [s.id for s in styles]
    labels = {s.id: s.label for s in styles}

    assert "bundled:brutalist" in ids
    assert "bundled:doom-metal" in ids
    assert "campaign:brutalist" in ids
    assert "campaign:custom" in ids
    assert not any(s.stem.startswith("_") or s.id.endswith(":_blank") for s in styles)
    assert labels["bundled:brutalist"] == "brutalist"
    assert labels["campaign:brutalist"] == "brutalist (campaign)"
    assert labels["campaign:custom"] == "custom (campaign)"


def test_list_art_styles_empty_campaign_still_returns_bundled(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _write_style(bundled / "brutalist.json")
    monkeypatch.setattr("art_styles.bundled_art_direction_dir", lambda: bundled)

    styles = list_art_styles(tmp_path / "campaigns", "missing")
    assert [s.id for s in styles] == ["bundled:brutalist"]


def test_resolve_art_style_by_id(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    path = bundled / "brutalist.json"
    _write_style(path)
    monkeypatch.setattr("art_styles.bundled_art_direction_dir", lambda: bundled)

    option = resolve_art_style(tmp_path / "campaigns", "flail", "bundled:brutalist")
    assert option.path == path
    assert option.source == "bundled"


def test_resolve_art_style_unknown_id_fails(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr("art_styles.bundled_art_direction_dir", lambda: bundled)

    with pytest.raises(ValueError, match="Unknown art style"):
        resolve_art_style(tmp_path / "campaigns", "flail", "bundled:missing")


def test_default_art_style_prefers_campaign_when_present(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _write_style(bundled / "brutalist.json", "Bundled.")
    monkeypatch.setattr("art_styles.bundled_art_direction_dir", lambda: bundled)

    camp_dir = campaign_art_direction_dir(tmp_path / "campaigns", "flail")
    _write_style(camp_dir / "custom.json", "Campaign only.")

    option = default_art_style(tmp_path / "campaigns", "flail")
    assert option.id == "campaign:custom"


def test_default_art_style_falls_back_to_bundled_brutalist(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _write_style(bundled / "other.json", "Other.")
    _write_style(bundled / f"{DEFAULT_ART_STYLE_STEM}.json", "Default.")
    monkeypatch.setattr("art_styles.bundled_art_direction_dir", lambda: bundled)

    option = default_art_style(tmp_path / "campaigns", "empty")
    assert option.id == f"bundled:{DEFAULT_ART_STYLE_STEM}"
    assert option.stem == DEFAULT_ART_STYLE_STEM


def test_art_style_option_is_frozen():
    option = ArtStyleOption(
        id="bundled:brutalist",
        stem="brutalist",
        label="brutalist",
        path=Path("/tmp/brutalist.json"),
        source="bundled",
    )
    with pytest.raises(Exception):
        option.stem = "nope"  # type: ignore[misc]
