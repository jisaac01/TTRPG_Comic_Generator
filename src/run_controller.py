"""Controller that launches ComicPipeline runs and streams structured events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pipeline import ComicPipeline
from pipeline_config import RunConfig
from pipeline_events import PipelineEventUnion, RunCompleted, VersionCreated


class PipelineRunner(Protocol):
    async def run(self) -> dict[str, object]:
        ...


PipelineFactory = Callable[..., PipelineRunner]
RunState = Literal["running", "ok", "partial", "failed", "cancelled"]


@dataclass
class RunInfo:
    config: RunConfig
    status: RunState
    started_at: datetime
    events: list[PipelineEventUnion] = field(default_factory=list)
    version: str | None = None
    version_dir: str | None = None
    failed_phases: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    status: RunState
    version: str | None
    version_dir: str | None
    failed_phases: list[str]
    errors: list[str]
    events: list[PipelineEventUnion]
    output: dict[str, object] | None


class RunController:
    def __init__(self, pipeline_factory: PipelineFactory = ComicPipeline) -> None:
        self._pipeline_factory = pipeline_factory
        self._active_task: asyncio.Task[RunResult] | None = None
        self._active_run: RunInfo | None = None
        self._last_result: RunResult | None = None

    def launch_run(
        self,
        config: RunConfig,
        event_callback: Callable[[PipelineEventUnion], None],
    ) -> asyncio.Task[RunResult]:
        if self._active_task is not None and not self._active_task.done():
            raise RuntimeError("A run is already in progress.")

        validation_errors = config.validate()
        if validation_errors:
            raise ValueError("Invalid RunConfig: " + "; ".join(validation_errors))

        run_info = RunInfo(
            config=config,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        def emit(event: PipelineEventUnion) -> None:
            run_info.events.append(event)
            if isinstance(event, VersionCreated):
                run_info.version = event.version
                run_info.version_dir = event.version_dir
            if isinstance(event, RunCompleted):
                run_info.status = event.status
                run_info.version = event.version
                run_info.version_dir = event.version_dir
                run_info.failed_phases = list(event.failed_phases)
                run_info.errors = list(event.error_messages)
            event_callback(event)

        pipeline = self._pipeline_factory(
            url=config.url,
            campaign=config.campaign,
            campaigns_root=config.campaigns_root,
            beater_model=config.beater_model,
            script_model=config.script_model,
            style_model=config.style_model,
            panel_count=config.panel_count,
            total_pages=config.total_pages,
            art_style_template=config.art_style_template,
            master_beater_system_prompt=config.master_beater_system_prompt,
            master_beater_user_prompt=config.master_beater_user_prompt,
            scriptwriter_system_prompt=config.scriptwriter_system_prompt,
            scriptwriter_user_prompt=config.scriptwriter_user_prompt,
            style_integrator_system_prompt=config.style_integrator_system_prompt,
            style_integrator_user_prompt=config.style_integrator_user_prompt,
            page_prompt_template=config.page_prompt_template,
            rerun_from=config.rerun_from,
            recap_version=config.recap_version,
            skip_style=config.skip_style,
            event_callback=emit,
        )

        task = asyncio.create_task(self._run_pipeline(pipeline, run_info))
        self._active_task = task
        self._active_run = run_info
        task.add_done_callback(self._on_run_finished)
        return task

    def current_run(self) -> RunInfo | None:
        if self._active_task is None or self._active_task.done():
            return None
        return self._active_run

    async def cancel_run(self) -> bool:
        if self._active_task is None or self._active_task.done():
            return False
        self._active_task.cancel()
        try:
            await self._active_task
        except asyncio.CancelledError:
            # Defensive: _run_pipeline handles cancellation and returns a result.
            pass
        return True

    def last_result(self) -> RunResult | None:
        return self._last_result

    async def _run_pipeline(self, pipeline: PipelineRunner, run_info: RunInfo) -> RunResult:
        try:
            output = await pipeline.run()
            if run_info.status == "running":
                output_errors = [str(err) for err in output.get("errors", [])] if output else []
                run_info.status = "ok" if not output_errors else "partial"
                run_info.errors = output_errors
                run_info.version = str(output.get("version")) if output.get("version") is not None else None
                run_info.version_dir = (
                    str(output.get("version_dir"))
                    if output.get("version_dir") is not None
                    else None
                )

            return RunResult(
                status=run_info.status,
                version=run_info.version,
                version_dir=run_info.version_dir,
                failed_phases=list(run_info.failed_phases),
                errors=list(run_info.errors),
                events=list(run_info.events),
                output=output,
            )
        except asyncio.CancelledError:
            run_info.status = "cancelled"
            return RunResult(
                status="cancelled",
                version=run_info.version,
                version_dir=run_info.version_dir,
                failed_phases=list(run_info.failed_phases),
                errors=list(run_info.errors),
                events=list(run_info.events),
                output=None,
            )
        except Exception as exc:
            run_info.status = "failed"
            run_info.errors.append(str(exc))
            return RunResult(
                status="failed",
                version=run_info.version,
                version_dir=run_info.version_dir,
                failed_phases=list(run_info.failed_phases),
                errors=list(run_info.errors),
                events=list(run_info.events),
                output=None,
            )

    def _on_run_finished(self, task: asyncio.Task[RunResult]) -> None:
        try:
            self._last_result = task.result()
        except Exception:
            self._last_result = None
        finally:
            self._active_task = None
            self._active_run = None