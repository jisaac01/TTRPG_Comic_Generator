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

RerunFrom = Literal["scrape", "entities", "architect", "script", "style", "prompt"]
RecapVersion = Literal["short", "standard", "alternate", "long"]
AspectRatio = Literal["1:1", "4:3", "3:2"]
GenerationMode = Literal["page", "panel"]

ASPECT_RATIO_NAMES: dict[str, str] = {
    "1:1": "Square",
    "4:3": "Vertical",
    "3:2": "Horizontal",
}


def aspect_ratio_display_label(aspect_ratio: str) -> str:
    return f"{aspect_ratio} — {ASPECT_RATIO_NAMES[aspect_ratio]}"


def format_aspect_ratio_for_prompt(aspect_ratio: str) -> str:
    return f"{aspect_ratio} {ASPECT_RATIO_NAMES[aspect_ratio].lower()}"

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

    # Root directory for campaign data (default: app_paths.default_campaigns_root())
    campaigns_root: Path = field(default_factory=lambda: CAMPAIGNS_ROOT)

    # Output structure
    generate_images: bool = False
    image_generation_model: str = "gemini-2.5-flash-image"

    # Output structure
    panel_count: int = 6
    total_pages: int = 1
    aspect_ratio: AspectRatio = "3:2"
    generation_mode: GenerationMode = "page"
    # When True, story architect focuses on one tight moment (micro-beats), not the full recap.
    # Orthogonal to generation_mode (page vs panel image layout).
    vignette: bool = False
    # When True, the final page/panel image prompt gets a unique prefix to bust Gemini Chat cache.
    cache_buster: bool = True
    # When True, also write a parallel final prompt from the unstyled script.
    unstyled_prompts: bool = False
    # When True, style header only on the first page/panel; later pages skip cache buster.
    chat_mode: bool = False
    # When True, tone down violence in script, style, and final prompt stages.
    pg13_mode: bool = False

    # Optional template/prompt overrides (explicit paths)
    art_style_template: Path | None = None
    # Selector id: "bundled:<stem>" or "campaign:<stem>" (resolved by pipeline)
    art_style: str | None = None
    story_architect_system_prompt: Path | None = None
    story_architect_user_prompt: Path | None = None
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
            "story_architect_system_prompt",
            "story_architect_user_prompt",
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
        if not isinstance(self.vignette, bool):
            errors.append("vignette must be a boolean")
        if not isinstance(self.cache_buster, bool):
            errors.append("cache_buster must be a boolean")
        if not isinstance(self.unstyled_prompts, bool):
            errors.append("unstyled_prompts must be a boolean")
        if not isinstance(self.chat_mode, bool):
            errors.append("chat_mode must be a boolean")
        if not isinstance(self.pg13_mode, bool):
            errors.append("pg13_mode must be a boolean")
        if self.art_style_template is not None and not self.art_style_template.exists():
            errors.append(f"art_style_template path does not exist: {self.art_style_template}")
        path_fields = [
            ("story_architect_system_prompt", self.story_architect_system_prompt),
            ("story_architect_user_prompt", self.story_architect_user_prompt),
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
    "architect",
    "script",
    "style",
    "prompt",
]

# Defaults used when comparing older run_status snapshots that omit newer keys.
SETTING_COMPARE_DEFAULTS: dict[str, object] = {
    "vignette": False,
    "cache_buster": True,
    "unstyled_prompts": False,
    "chat_mode": False,
    "pg13_mode": False,
}

SETTING_MIN_STAGE: dict[str, RerunFrom] = {
    # Recap body text feeds the story bible, not keyed entity extraction.
    "recap_version": "architect",
    "panel_count": "architect",
    "total_pages": "architect",
    "vignette": "architect",
    "generation_mode": "script",
    "art_style": "style",
    "aspect_ratio": "prompt",
    "cache_buster": "prompt",
    "unstyled_prompts": "prompt",
    "chat_mode": "prompt",
    "pg13_mode": "script",
}

SETTING_FIELD_MIN_STAGE: dict[str, RerunFrom] = {
    "recap": "architect",
    "panels": "architect",
    "pages": "architect",
    "vignette": "architect",
    "generation_mode": "script",
    "art_style": "style",
    "aspect_ratio": "prompt",
    "cache_buster": "prompt",
    "unstyled_prompts": "prompt",
    "chat_mode": "prompt",
    "pg13_mode": "script",
}

PROMPT_AFFECTING_KEYS = frozenset(
    {
        "aspect_ratio",
        "generation_mode",
        "panel_count",
        "total_pages",
        "art_style",
        "vignette",
        "cache_buster",
        "unstyled_prompts",
        "chat_mode",
        "pg13_mode",
    }
)

RUN_CONFIG_KEYS = (
    "panel_count",
    "total_pages",
    "recap_version",
    "aspect_ratio",
    "generation_mode",
    "vignette",
    "cache_buster",
    "unstyled_prompts",
    "chat_mode",
    "pg13_mode",
    "art_style",
    "generate_images",
    "image_generation_model",
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
        "vignette": config.vignette,
        "cache_buster": config.cache_buster,
        "unstyled_prompts": config.unstyled_prompts,
        "chat_mode": config.chat_mode,
        "pg13_mode": config.pg13_mode,
        "art_style": config.art_style,
        "generate_images": config.generate_images,
        "image_generation_model": config.image_generation_model,
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


def _config_value(config: dict, key: str) -> object:
    """Read a run-config value, applying defaults for keys older snapshots omit."""
    if key in config:
        return config[key]
    return SETTING_COMPARE_DEFAULTS.get(key)


def earliest_stage_for_config_diff(
    prev_config: dict | None,
    new_config: dict,
) -> RerunFrom | None:
    """Return the earliest pipeline stage invalidated by changed run settings."""
    if not prev_config:
        return None

    earliest_idx = len(STAGE_ORDER)
    for key, min_stage in SETTING_MIN_STAGE.items():
        if _config_value(prev_config, key) != _config_value(new_config, key):
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
        if _config_value(prev_config, key) != _config_value(new_config, key):
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
