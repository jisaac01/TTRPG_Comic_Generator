"""Structured configuration for ComicPipeline runs.

This module provides a dataclass that captures all pipeline configuration options,
making it easy to serialize/deserialize run configs and pass them between the CLI,
GUI, and other consumers without reconstructing argument lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from model_defaults import DEFAULT_MODEL

RerunFrom = Literal["scrape", "entities", "beater", "script", "style", "prompt"]
RecapVersion = Literal["short", "standard", "alternate", "long"]

CAMPAIGNS_ROOT = Path("campaigns")


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

    # Model selection (each stage can use a different model)
    beater_model: str = field(default_factory=lambda: DEFAULT_MODEL)
    script_model: str = field(default_factory=lambda: DEFAULT_MODEL)
    style_model: str = field(default_factory=lambda: DEFAULT_MODEL)

    # Output structure
    panel_count: int = 6
    total_pages: int = 1

    # Optional template/prompt overrides (explicit paths)
    art_style_template: Path | None = None
    master_beater_system_prompt: Path | None = None
    master_beater_user_prompt: Path | None = None
    scriptwriter_system_prompt: Path | None = None
    scriptwriter_user_prompt: Path | None = None
    style_integrator_system_prompt: Path | None = None
    style_integrator_user_prompt: Path | None = None
    page_prompt_template: Path | None = None

    # Rerun control
    rerun_from: RerunFrom | None = None
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
