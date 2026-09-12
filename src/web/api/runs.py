"""Launch, inspect, stream, and cancel pipeline runs."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from pipeline_config import RunConfig
from web.schemas import RunLaunchRequest, RunSnapshotResponse
from web.run_store import RunStore, TrackedRun

router = APIRouter()


def _store(request: Request) -> RunStore:
    return request.app.state.runs


def _snapshot_response(run: TrackedRun) -> RunSnapshotResponse:
    return RunSnapshotResponse(**run.snapshot())


def _build_config(request: Request, body: RunLaunchRequest) -> RunConfig:
    settings = request.app.state.services.settings
    return RunConfig(
        url=body.url,
        campaign=body.campaign,
        campaigns_root=request.app.state.campaigns_root,
        generate_images=body.generate_images,
        image_generation_model=settings.get_image_generation_model(),
        panel_count=body.panel_count,
        total_pages=body.total_pages,
        aspect_ratio=body.aspect_ratio,  # type: ignore[arg-type]
        generation_mode=body.generation_mode,  # type: ignore[arg-type]
        vignette=body.vignette,
        cache_buster=body.cache_buster,
        unstyled_prompts=body.unstyled_prompts,
        chat_mode=body.chat_mode,
        pg13_mode=body.pg13_mode,
        art_style=body.art_style,
        rerun_from=body.rerun_from,  # type: ignore[arg-type]
        stop_after=body.stop_after,  # type: ignore[arg-type]
        recap_version=body.recap_version,  # type: ignore[arg-type]
    )


@router.post("/api/runs", response_model=RunSnapshotResponse, status_code=202)
async def launch_run(body: RunLaunchRequest, request: Request) -> RunSnapshotResponse:
    controller = request.app.state.services.run_controller
    store = _store(request)
    run_id = uuid.uuid4().hex[:12]
    tracked = TrackedRun(id=run_id)

    def on_event(event: Any) -> None:
        tracked.apply_event(event)

    try:
        task = controller.launch_run(_build_config(request, body), on_event)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store.runs[run_id] = tracked
    store.current_id = run_id

    def _on_done(_task: asyncio.Task) -> None:
        result = controller.last_result()
        if result is not None:
            tracked.status = result.status
            tracked.version = result.version or tracked.version
            tracked.failed_phases = list(result.failed_phases)
            tracked.errors = list(result.errors)
        elif tracked.status == "running":
            tracked.status = "failed"
        store.clear_current(run_id)

    task.add_done_callback(_on_done)
    return _snapshot_response(tracked)


@router.get("/api/runs/current", response_model=RunSnapshotResponse)
def current_run(request: Request) -> RunSnapshotResponse:
    store = _store(request)
    if store.current_id is None:
        return RunSnapshotResponse(id=None, status="idle")
    tracked = store.get(store.current_id)
    if tracked is None:
        return RunSnapshotResponse(id=None, status="idle")
    return _snapshot_response(tracked)


@router.get("/api/runs/{run_id}", response_model=RunSnapshotResponse)
def get_run(run_id: str, request: Request) -> RunSnapshotResponse:
    tracked = _store(request).get(run_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail="run was not found")
    return _snapshot_response(tracked)


@router.get("/api/runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
    store = _store(request)
    tracked = store.get(run_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail="run was not found")

    async def generate():
        sent = 0
        while True:
            while sent < len(tracked.events):
                yield f"data: {json.dumps(tracked.events[sent])}\n\n"
                sent += 1
            if tracked.status != "running":
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/api/runs/{run_id}/cancel", response_model=RunSnapshotResponse)
async def cancel_run(run_id: str, request: Request) -> RunSnapshotResponse:
    store = _store(request)
    tracked = store.get(run_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail="run was not found")
    if store.current_id != run_id:
        raise HTTPException(status_code=409, detail="run is not active")
    await request.app.state.services.run_controller.cancel_run()
    return _snapshot_response(tracked)
