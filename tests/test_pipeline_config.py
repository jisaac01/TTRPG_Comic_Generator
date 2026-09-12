from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from pipeline_config import (
    RunConfig,
    aspect_ratio_display_label,
    effective_rerun_from,
    format_aspect_ratio_for_prompt,
    run_config_snapshot,
    setting_field_enabled,
    should_copy_prompt_artifacts,
)


def test_aspect_ratio_display_labels_use_orientation_names() -> None:
    assert aspect_ratio_display_label("1:1") == "1:1 — Square"
    assert aspect_ratio_display_label("4:3") == "4:3 — Vertical"
    assert aspect_ratio_display_label("3:2") == "3:2 — Horizontal"


def test_format_aspect_ratio_for_prompt_includes_orientation_words() -> None:
    assert format_aspect_ratio_for_prompt("1:1") == "1:1 square"
    assert format_aspect_ratio_for_prompt("4:3") == "4:3 vertical"
    assert format_aspect_ratio_for_prompt("3:2") == "3:2 horizontal"


def test_run_config_defaults_generate_images_off_and_uses_current_schema() -> None:
    config = RunConfig(url="https://example.test/story", campaign="dreadmarsh")

    assert config.generate_images is False
    assert not hasattr(config, "architect_model")
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


def test_run_config_defaults_vignette_off() -> None:
    config = RunConfig(url="https://example.test/story", campaign="dreadmarsh")

    assert config.vignette is False


def test_run_config_defaults_cache_buster_on() -> None:
    config = RunConfig(url="https://example.test/story", campaign="dreadmarsh")

    assert config.cache_buster is True


def test_run_config_defaults_feature_toggles_off() -> None:
    config = RunConfig(url="https://example.test/story", campaign="dreadmarsh")

    assert config.unstyled_prompts is False
    assert config.chat_mode is False
    assert config.pg13_mode is False


def test_run_config_round_trip_preserves_generation_mode() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        generation_mode="panel",
    )

    restored = RunConfig.from_dict(config.to_dict())

    assert restored.generation_mode == "panel"


def test_run_config_round_trip_preserves_vignette() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        vignette=True,
    )

    restored = RunConfig.from_dict(config.to_dict())

    assert restored.vignette is True


def test_run_config_snapshot_includes_vignette() -> None:
    snap = run_config_snapshot(
        RunConfig(
            url="https://example.test/story",
            campaign="dreadmarsh",
            vignette=True,
        )
    )
    assert snap["vignette"] is True


def test_run_config_round_trip_preserves_cache_buster() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        cache_buster=False,
    )

    restored = RunConfig.from_dict(config.to_dict())

    assert restored.cache_buster is False


def test_run_config_snapshot_includes_cache_buster() -> None:
    snap = run_config_snapshot(
        RunConfig(
            url="https://example.test/story",
            campaign="dreadmarsh",
            cache_buster=False,
        )
    )
    assert snap["cache_buster"] is False


def test_run_config_round_trip_preserves_feature_toggles() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        unstyled_prompts=True,
        chat_mode=True,
        pg13_mode=True,
    )

    restored = RunConfig.from_dict(config.to_dict())

    assert restored.unstyled_prompts is True
    assert restored.chat_mode is True
    assert restored.pg13_mode is True


def test_run_config_snapshot_includes_feature_toggles() -> None:
    snap = run_config_snapshot(
        RunConfig(
            url="https://example.test/story",
            campaign="dreadmarsh",
            unstyled_prompts=True,
            chat_mode=True,
            pg13_mode=True,
        )
    )
    assert snap["unstyled_prompts"] is True
    assert snap["chat_mode"] is True
    assert snap["pg13_mode"] is True


def test_run_config_snapshot_includes_image_generation_model() -> None:
    snap = run_config_snapshot(
        RunConfig(
            url="https://example.test/story",
            campaign="dreadmarsh",
            generate_images=True,
            image_generation_model="gemini-3.1-flash-image",
        )
    )
    assert snap["generate_images"] is True
    assert snap["image_generation_model"] == "gemini-3.1-flash-image"


def test_effective_rerun_from_bumps_when_generation_mode_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    new = dict(prev)
    new["generation_mode"] = "panel"

    assert effective_rerun_from("prompt", prev, new) == "script"


def test_effective_rerun_from_bumps_to_architect_when_vignette_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    new = dict(prev)
    new["vignette"] = True

    assert effective_rerun_from("prompt", prev, new) == "architect"
    assert effective_rerun_from("script", prev, new) == "architect"
    assert effective_rerun_from("architect", prev, new) == "architect"
    assert effective_rerun_from("entities", prev, new) == "entities"


def test_effective_rerun_from_treats_missing_vignette_as_false() -> None:
    """Older run_status snapshots omit vignette; that equals the default off state."""
    from pipeline_config import earliest_stage_for_config_diff

    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    prev.pop("vignette", None)
    new = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh", vignette=False)
    )

    assert earliest_stage_for_config_diff(prev, new) is None
    assert effective_rerun_from(None, prev, new) is None


def test_setting_field_enabled_allows_vignette_at_architect_not_script() -> None:
    assert setting_field_enabled("vignette", "architect") is True
    assert setting_field_enabled("vignette", "entities") is True
    assert setting_field_enabled("vignette", "script") is False


def test_effective_rerun_from_bumps_to_prompt_when_cache_buster_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    new = dict(prev)
    new["cache_buster"] = False

    assert effective_rerun_from(None, prev, new) == "prompt"
    assert effective_rerun_from("prompt", prev, new) == "prompt"
    assert effective_rerun_from("style", prev, new) == "style"
    assert effective_rerun_from("architect", prev, new) == "architect"


def test_effective_rerun_from_treats_missing_cache_buster_as_true() -> None:
    """Older run_status snapshots omit cache_buster; that equals the default on state."""
    from pipeline_config import earliest_stage_for_config_diff

    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    prev.pop("cache_buster", None)
    new = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh", cache_buster=True)
    )

    assert earliest_stage_for_config_diff(prev, new) is None
    assert effective_rerun_from(None, prev, new) is None


def test_setting_field_enabled_allows_cache_buster_at_prompt() -> None:
    assert setting_field_enabled("cache_buster", "prompt") is True
    assert setting_field_enabled("cache_buster", "style") is True
    assert setting_field_enabled("cache_buster", "architect") is True


def test_effective_rerun_from_feature_toggles() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )

    unstyled = dict(prev)
    unstyled["unstyled_prompts"] = True
    assert effective_rerun_from(None, prev, unstyled) == "prompt"
    assert effective_rerun_from("style", prev, unstyled) == "style"

    chat = dict(prev)
    chat["chat_mode"] = True
    assert effective_rerun_from(None, prev, chat) == "prompt"

    pg13 = dict(prev)
    pg13["pg13_mode"] = True
    assert effective_rerun_from(None, prev, pg13) == "script"
    assert effective_rerun_from("prompt", prev, pg13) == "script"
    assert effective_rerun_from("architect", prev, pg13) == "architect"


def test_effective_rerun_from_treats_missing_feature_toggles_as_false() -> None:
    from pipeline_config import earliest_stage_for_config_diff

    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    prev.pop("unstyled_prompts", None)
    prev.pop("chat_mode", None)
    prev.pop("pg13_mode", None)
    new = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )

    assert earliest_stage_for_config_diff(prev, new) is None
    assert effective_rerun_from(None, prev, new) is None


def test_setting_field_enabled_for_feature_toggles() -> None:
    assert setting_field_enabled("unstyled_prompts", "prompt") is True
    assert setting_field_enabled("chat_mode", "prompt") is True
    assert setting_field_enabled("pg13_mode", "script") is True
    assert setting_field_enabled("pg13_mode", "architect") is True
    assert setting_field_enabled("pg13_mode", "style") is False
    assert setting_field_enabled("pg13_mode", "prompt") is False


def test_effective_rerun_from_bumps_when_panel_count_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh", panel_count=6)
    )
    new = dict(prev)
    new["panel_count"] = 8

    assert effective_rerun_from("style", prev, new) == "architect"


def test_effective_rerun_from_bumps_to_architect_when_recap_version_changes() -> None:
    prev = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )
    new = dict(prev)
    new["recap_version"] = "long"

    assert effective_rerun_from("prompt", prev, new) == "architect"
    assert effective_rerun_from("script", prev, new) == "architect"
    assert effective_rerun_from("architect", prev, new) == "architect"
    # Explicit earlier stage still wins (min of requested and config).
    assert effective_rerun_from("entities", prev, new) == "entities"


def test_setting_field_enabled_allows_recap_at_architect_not_script() -> None:
    assert setting_field_enabled("recap", "architect") is True
    assert setting_field_enabled("recap", "entities") is True
    assert setting_field_enabled("recap", "scrape") is True
    assert setting_field_enabled("recap", "script") is False
    assert setting_field_enabled("recap", "prompt") is False


def test_should_copy_prompt_artifacts_only_when_config_unchanged() -> None:
    config = run_config_snapshot(
        RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    )

    assert should_copy_prompt_artifacts(None, config, config) is True
    assert should_copy_prompt_artifacts("prompt", config, config) is False

    changed = dict(config)
    changed["generation_mode"] = "panel"
    assert should_copy_prompt_artifacts(None, config, changed) is False

    cache_changed = dict(config)
    cache_changed["cache_buster"] = False
    assert should_copy_prompt_artifacts(None, config, cache_changed) is False

    unstyled = dict(config)
    unstyled["unstyled_prompts"] = True
    assert should_copy_prompt_artifacts(None, config, unstyled) is False

    chat = dict(config)
    chat["chat_mode"] = True
    assert should_copy_prompt_artifacts(None, config, chat) is False

    pg13 = dict(config)
    pg13["pg13_mode"] = True
    assert should_copy_prompt_artifacts(None, config, pg13) is False


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


def test_run_config_defaults_stop_after_none() -> None:
    config = RunConfig(url="https://example.test/story", campaign="dreadmarsh")
    assert config.stop_after is None


def test_run_config_round_trip_preserves_stop_after() -> None:
    config = RunConfig(
        url="https://example.test/story",
        campaign="dreadmarsh",
        rerun_from="entities",
        stop_after="entities",
    )
    restored = RunConfig.from_dict(config.to_dict())
    assert restored.stop_after == "entities"
    assert restored.rerun_from == "entities"


def test_run_config_snapshot_includes_stop_after() -> None:
    snap = run_config_snapshot(
        RunConfig(
            url="https://example.test/story",
            campaign="dreadmarsh",
            stop_after="architect",
        )
    )
    assert snap["stop_after"] == "architect"
