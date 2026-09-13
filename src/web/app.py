"""FastAPI factory for the localhost web app."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app_paths import default_campaigns_root
from repository_service import RepositoryService
from run_controller import RunController
from scraper import playwright_preflight_warnings
from settings_service import SettingsService
from web.api.campaigns import router as campaigns_router
from web.api.images import router as images_router
from web.api.prompts import router as prompts_router
from web.api.runs import router as runs_router
from web.api.settings import router as settings_router
from web.api.versions import router as versions_router
from web.run_store import RunStore
from web.schemas import HealthResponse

WEB_HOST = "127.0.0.1"
WEB_PORT = 8765
STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class AppServices:
    repository: RepositoryService
    settings: SettingsService
    run_controller: RunController


def create_services(
    campaigns_root: Path | None = None,
    *,
    settings_path: Path | None = None,
    run_controller: RunController | None = None,
) -> AppServices:
    resolved_root = campaigns_root or default_campaigns_root()
    return AppServices(
        repository=RepositoryService(resolved_root),
        settings=SettingsService(config_path=settings_path),
        run_controller=run_controller or RunController(),
    )


def create_app(
    *,
    campaigns_root: Path | None = None,
    settings_path: Path | None = None,
    run_controller: RunController | None = None,
    services: AppServices | None = None,
) -> FastAPI:
    resolved_services = services or create_services(
        campaigns_root,
        settings_path=settings_path,
        run_controller=run_controller,
    )
    app = FastAPI(title="TTRPG Comic Generator")
    app.state.services = resolved_services
    app.state.campaigns_root = resolved_services.repository.campaigns_root
    app.state.catalog_path = resolved_services.settings.config_path.with_name("models.json")
    app.state.runs = RunStore()

    @app.get("/")
    def landing() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", warnings=playwright_preflight_warnings())

    app.include_router(campaigns_router)
    app.include_router(prompts_router)
    app.include_router(versions_router)
    app.include_router(settings_router)
    app.include_router(runs_router)
    app.include_router(images_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
