from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from pipeline_config import RunConfig


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
