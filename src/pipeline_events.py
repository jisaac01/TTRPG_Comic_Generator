"""Structured event types emitted by ComicPipeline during execution.

The pipeline.run() method will emit these events instead of calling print() directly.
This allows both the CLI (as a consumer that prints events) and GUI (as a consumer that
updates UI) to receive the same structured information.

Events are emitted via a callback function passed to the pipeline, allowing flexible
handling and multiple listeners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Union


PhaseName = Literal[
    "scrape",
    "entities",
    "beater",
    "script",
    "style",
    "prompt",
]


@dataclass
class PipelineEvent:
    """Base class for all pipeline events."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    phase: PhaseName | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to dictionary."""
        return {
            "type": self.__class__.__name__,
            "timestamp": self.timestamp.isoformat(),
            "phase": self.phase,
            **{k: v for k, v in self.__dict__.items() if k not in ("timestamp", "phase")},
        }


@dataclass
class PhaseStarted(PipelineEvent):
    """Emitted when a pipeline phase begins execution."""

    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    # Example:
    #   phase="scrape", message="Scraping...", details={"url": "..."}
    #   phase="beater", message="Creating story bible...", details={"model": "gemini-3.1-flash-lite", "scene_count": 6}


@dataclass
class PhaseSkipped(PipelineEvent):
    """Emitted when a phase is skipped because its output already exists."""

    message: str = ""
    reason: str = ""  # e.g., "checkpoint exists", "no input data"

    # Example:
    #   phase="entities", message="Building entities...skipped", reason="checkpoint exists"


@dataclass
class PhaseCompleted(PipelineEvent):
    """Emitted when a phase completes successfully."""

    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    # Example:
    #   phase="scrape", message="...done", details={"title": "Dreadmarsh Crossing", "recap": "standard"}
    #   phase="script", message="...done", details={"page_count": 1}


@dataclass
class PhaseWarning(PipelineEvent):
    """Emitted when a phase completes with a non-fatal issue."""

    message: str = ""
    warning: str = ""

    # Example:
    #   phase="style", message="...WARN", warning="Style integration partially failed on page 1: ..."


@dataclass
class PhaseError(PipelineEvent):
    """Emitted when a phase fails with a fatal error; execution halts after this phase."""

    message: str = ""
    error: str = ""
    exception: Exception | None = None

    # Example:
    #   phase="beater", message="...ERROR", error="story_bible: API rate limit exceeded", exception=<RateLimitError>


@dataclass
class PhasePartialFailure(PipelineEvent):
    """Emitted when a phase partially succeeds but downstream phases are skipped due to missing input."""

    message: str = ""
    skipped_phases: list[PhaseName] = field(default_factory=list)
    error_detail: str = ""

    # Example:
    #   phase="script", message="...ERROR (script generation failed)", skipped_phases=["style", "prompt"], error_detail="..."


@dataclass
class VersionCreated(PipelineEvent):
    """Emitted when a new version directory is created and registered."""

    version: str = ""  # e.g., "v001", "v003"
    version_dir: str = ""  # Full path to the version directory
    episode_slug: str = ""  # e.g., "dreadmarsh-crossing"

    # Example:
    #   version="v001", version_dir="/.../campaigns/dreadmarsh/dreadmarsh-crossing/v001", episode_slug="dreadmarsh-crossing"


@dataclass
class RunCompleted(PipelineEvent):
    """Emitted when the entire run is complete (successful or with errors)."""

    status: Literal["ok", "partial", "failed"] = "ok"
    version: str = ""  # e.g., "v001"
    version_dir: str = ""
    checkpoints: list[str] = field(default_factory=list)  # e.g., ["raw_text", "entities", "script", ...]
    failed_phases: list[PhaseName] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)

    # Example:
    #   status="partial", version="v001", version_dir="...", checkpoints=["raw_text", "entities", "story_bible"], failed_phases=["script"], error_messages=["script: Panel count mismatch..."]


# Union of all possible event types
PipelineEventUnion = Union[
    PhaseStarted,
    PhaseSkipped,
    PhaseCompleted,
    PhaseWarning,
    PhaseError,
    PhasePartialFailure,
    VersionCreated,
    RunCompleted,
]

PipelineEventCallback = Callable[[PipelineEventUnion], None]
