"""Structured configuration for ComicPipeline runs.

This module provides a dataclass that captures all pipeline configuration options,
making it easy to serialize/deserialize run configs and pass them between the CLI,
GUI, and other consumers without reconstructing argument lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app_paths import default_campaigns_root

RerunFrom = Literal["scrape", "entities", "beater", "script", "style", "prompt"]
RecapVersion = Literal["short", "standard", "alternate", "long"]
AspectRatio = Literal["1:1", "4:3", "3:2"]
GenerationMode = Literal["page", "panel"]

CAMPAIGNS_ROOT = default_campaigns_root()


@dataclass
class RunConfig:
    """Configuration for a single ComicPipeline execution.
    
    All fields correspond directly to ComicPipeline.__init__ parameters.
    Paths are stored as Path objects but can be serialized to/from strings.
    """

    # Required parameters
    url: str
    campaign: str

    # Root directory for campaign data (default: campaigns/)
    campaigns_root: Path = field(default_factory=lambda: CAMPAIGNS_ROOT)

    # Output structure
    generate_images: bool = False
    image_generation_model: str = "gemini-2.5-flash-image"

    # Output structure
    panel_count: int = 6
    total_pages: int = 1
    aspect_ratio: AspectRatio = "3:2"
    generation_mode: GenerationMode = "page"

    # Optional template/prompt overrides (explicit paths)
    art_style_template: Path | None = None
    # Selector id: "bundled:<stem>" or "campaign:<stem>" (resolved by pipeline)
    art_style: str | None = None
    master_beater_system_prompt: Path | None = None
    master_beater_user_prompt: Path | None = None
    scriptwriter_system_prompt: Path | None = None
    scriptwriter_user_prompt: Path | None = None
    style_integrator_system_prompt: Path | None = None
    style_integrator_user_prompt: Path | None = None
    page_prompt_template: Path | None = None

    # Rerun control
    rerun_from: RerunFrom | None = None
    # When set, run stages through this phase (inclusive) then stop.
    stop_after: RerunFrom | None = None
    recap_version: RecapVersion = "standard"
    skip_style: bool = False

    def to_dict(self) -> dict:
        """Serialize to dictionary, converting Path objects to strings."""
        data = {}
        for key, value in self.__dict__.items():
            if isinstance(value, Path):
                data[key] = str(value)
            else:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> RunConfig:
        """Deserialize from dictionary, converting string paths to Path objects."""
        data_copy = dict(data)
        path_fields = {
            "campaigns_root",
            "art_style_template",
            "master_beater_system_prompt",
            "master_beater_user_prompt",
            "scriptwriter_system_prompt",
            "scriptwriter_user_prompt",
            "style_integrator_system_prompt",
            "style_integrator_user_prompt",
            "page_prompt_template",
        }
        for field_name in path_fields:
            if field_name in data_copy and data_copy[field_name] is not None:
                data_copy[field_name] = Path(data_copy[field_name])
        return cls(**data_copy)

    def validate(self) -> list[str]:
        """Validate configuration; return list of error messages (empty if valid)."""
        errors = []
        if not self.url.strip():
            errors.append("url is required and cannot be empty")
        if not self.campaign.strip():
            errors.append("campaign is required and cannot be empty")
        if self.panel_count <= 0:
            errors.append("panel_count must be > 0")
        if self.total_pages <= 0:
            errors.append("total_pages must be > 0")
        if self.aspect_ratio not in {"1:1", "4:3", "3:2"}:
            errors.append("aspect_ratio must be one of 1:1, 4:3, 3:2")
        if self.generation_mode not in {"page", "panel"}:
            errors.append("generation_mode must be either 'page' or 'panel'")
        if self.art_style_template is not None and not self.art_style_template.exists():
            errors.append(f"art_style_template path does not exist: {self.art_style_template}")
        path_fields = [
            ("master_beater_system_prompt", self.master_beater_system_prompt),
            ("master_beater_user_prompt", self.master_beater_user_prompt),
            ("scriptwriter_system_prompt", self.scriptwriter_system_prompt),
            ("scriptwriter_user_prompt", self.scriptwriter_user_prompt),
            ("style_integrator_system_prompt", self.style_integrator_system_prompt),
            ("style_integrator_user_prompt", self.style_integrator_user_prompt),
            ("page_prompt_template", self.page_prompt_template),
        ]
        for field_name, path_value in path_fields:
            if path_value is not None and not path_value.exists():
                errors.append(f"{field_name} path does not exist: {path_value}")
        return errors


STAGE_ORDER: list[RerunFrom] = [
    "scrape",
    "entities",
    "beater",
    "script",
    "style",
    "prompt",
]

SETTING_MIN_STAGE: dict[str, RerunFrom] = {
    # Recap body text feeds the story bible, not keyed entity extraction.
    "recap_version": "beater",
    "panel_count": "beater",
    "total_pages": "beater",
    "generation_mode": "script",
    "art_style": "style",
    "aspect_ratio": "prompt",
}

SETTING_FIELD_MIN_STAGE: dict[str, RerunFrom] = {
    "recap": "beater",
    "panels": "beater",
    "pages": "beater",
    "generation_mode": "script",
    "art_style": "style",
    "aspect_ratio": "prompt",
}

PROMPT_AFFECTING_KEYS = frozenset(
    {"aspect_ratio", "generation_mode", "panel_count", "total_pages", "art_style"}
)

RUN_CONFIG_KEYS = (
    "panel_count",
    "total_pages",
    "recap_version",
    "aspect_ratio",
    "generation_mode",
    "art_style",
    "skip_style",
    "generate_images",
    "rerun_from",
    "stop_after",
)


def run_config_snapshot(config: RunConfig) -> dict:
    """Extract version-persisted settings from a RunConfig."""
    return {
        "panel_count": config.panel_count,
        "total_pages": config.total_pages,
        "recap_version": config.recap_version,
        "aspect_ratio": config.aspect_ratio,
        "generation_mode": config.generation_mode,
        "art_style": config.art_style,
        "skip_style": config.skip_style,
        "generate_images": config.generate_images,
        "rerun_from": config.rerun_from,
        "stop_after": config.stop_after,
    }


def _stage_index(stage: RerunFrom | None) -> int:
    if stage is None:
        return len(STAGE_ORDER)
    return STAGE_ORDER.index(stage)


def should_run_stage(stage: RerunFrom, stop_after: RerunFrom | None) -> bool:
    """Return whether *stage* should execute given an optional stop_after bound."""
    if stop_after is None:
        return True
    return _stage_index(stage) <= _stage_index(stop_after)


def earliest_stage_for_config_diff(
    prev_config: dict | None,
    new_config: dict,
) -> RerunFrom | None:
    """Return the earliest pipeline stage invalidated by changed run settings."""
    if not prev_config:
        return None

    earliest_idx = len(STAGE_ORDER)
    for key, min_stage in SETTING_MIN_STAGE.items():
        if prev_config.get(key) != new_config.get(key):
            earliest_idx = min(earliest_idx, _stage_index(min_stage))

    if earliest_idx >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[earliest_idx]


def effective_rerun_from(
    requested: RerunFrom | None,
    prev_config: dict | None,
    new_config: dict,
) -> RerunFrom | None:
    """Combine the requested rerun stage with config-driven invalidation."""
    requested_idx = _stage_index(requested)
    diff_stage = earliest_stage_for_config_diff(prev_config, new_config)
    diff_idx = _stage_index(diff_stage)
    effective_idx = min(requested_idx, diff_idx)
    if effective_idx >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[effective_idx]


def should_copy_prompt_artifacts(
    effective_rerun: RerunFrom | None,
    prev_config: dict | None,
    new_config: dict,
) -> bool:
    """Copy prompt files only when prompts are not being regenerated."""
    if effective_rerun is not None:
        return False
    if not prev_config:
        return False
    for key in PROMPT_AFFECTING_KEYS:
        if prev_config.get(key) != new_config.get(key):
            return False
    return True


def setting_field_enabled(field: str, rerun_stage: str) -> bool:
    """Return whether a settings field may be edited for the selected rerun stage."""
    min_stage = SETTING_FIELD_MIN_STAGE.get(field)
    if min_stage is None:
        return False
    return _stage_index(rerun_stage) <= _stage_index(min_stage)


def required_rerun_for_config_diff(
    prev_config: dict | None,
    new_config: dict,
) -> RerunFrom | None:
    """Earliest stage required when applying new_config over prev_config."""
    return earliest_stage_for_config_diff(prev_config, new_config)
