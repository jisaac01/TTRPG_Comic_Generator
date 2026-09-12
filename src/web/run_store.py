"""In-memory tracking for the single active localhost run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline_events import PipelineEventUnion, PhaseStarted, RunCompleted, VersionCreated


def public_event(event: PipelineEventUnion) -> dict[str, Any]:
    payload = event.to_dict()
    payload.pop("exception", None)
    payload.pop("version_dir", None)
    return payload


@dataclass
class TrackedRun:
    id: str
    status: str = "running"
    phase: str | None = None
    version: str | None = None
    failed_phases: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def apply_event(self, event: PipelineEventUnion) -> None:
        self.events.append(public_event(event))
        if isinstance(event, PhaseStarted):
            self.phase = event.phase
        if isinstance(event, VersionCreated):
            self.version = event.version
        if isinstance(event, RunCompleted):
            self.status = event.status
            self.version = event.version or self.version
            self.failed_phases = list(event.failed_phases)
            self.errors = list(event.error_messages)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "phase": self.phase,
            "version": self.version,
            "failed_phases": list(self.failed_phases),
            "errors": list(self.errors),
            "events": list(self.events),
        }


class RunStore:
    def __init__(self) -> None:
        self.current_id: str | None = None
        self.runs: dict[str, TrackedRun] = {}

    def get(self, run_id: str) -> TrackedRun | None:
        return self.runs.get(run_id)

    def clear_current(self, run_id: str) -> None:
        if self.current_id == run_id:
            self.current_id = None
