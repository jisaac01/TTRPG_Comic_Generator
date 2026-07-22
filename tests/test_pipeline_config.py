from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from pipeline_config import (
    RunConfig,
    effective_rerun_from,
    run_config_snapshot,
    should_copy_prompt_artifacts,
)


def test_run_config_defaults_generate_images_off_and_uses_current_schema() -> None:
    config = RunConfig(url="https://example.test/story", campaign="dreadmarsh")

    assert config.generate_images is False
    assert not hasattr(config, "beater_model")
    assert not hasattr(config, "script_model")
    assert not hasattr(config, "style_model")


def test_run_config_round_trip_preserves_generate_images() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        generate_images=True,
    )

    restored = RunConfig.from_dict(config.to_dict())

    assert restored.generate_images is True


def test_run_config_defaults_to_page_generation_mode() -> None:
    config = RunConfig(url="https://example.test/story", campaign="dreadmarsh")

    assert config.generation_mode == "page"


def test_run_config_round_trip_preserves_generation_mode() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        generation_mode="panel",
    )

    restored = RunConfig.from_dict(config.to_dict())

    assert restored.generation_mode == "panel"


def test_effective_rerun_from_bumps_when_generation_mode_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    new = dict(prev)
    new["generation_mode"] = "panel"

    assert effective_rerun_from("prompt", prev, new) == "script"


def test_effective_rerun_from_bumps_when_panel_count_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh", panel_count=6)
    )
    new = dict(prev)
    new["panel_count"] = 8

    assert effective_rerun_from("style", prev, new) == "beater"


def test_should_copy_prompt_artifacts_only_when_config_unchanged() -> None:
    config = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )

    assert should_copy_prompt_artifacts(None, config, config) is True
    assert should_copy_prompt_artifacts("prompt", config, config) is False

    changed = dict(config)
    changed["generation_mode"] = "panel"
    assert should_copy_prompt_artifacts(None, config, changed) is False


def test_run_config_round_trip_preserves_art_style() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        art_style="bundled:brutalist",
    )

    restored = RunConfig.from_dict(config.to_dict())

    assert restored.art_style == "bundled:brutalist"


def test_effective_rerun_from_bumps_when_art_style_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(
            url="https://example.test/story",
            campaign="dreadmarsh",
            art_style="bundled:brutalist",
        )
    )
    new = dict(prev)
    new["art_style"] = "campaign:custom"

    assert effective_rerun_from("prompt", prev, new) == "style"


def test_run_config_snapshot_includes_art_style() -> None:
    snap = run_config_snapshot(
        RunConfig(
            url="https://example.test/story",
            campaign="dreadmarsh",
            art_style="bundled:brutalist",
        )
    )
    assert snap["art_style"] == "bundled:brutalist"
