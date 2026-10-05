"""FastAPI application factory, API routes, and managed background services."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api_models import WebsiteCreate, WebsiteUpdate
from app.checks import CheckRunner
from app.config import Settings
from app.database import (
    DuplicateWebsite,
    Repository,
    WebsiteNotFound,
    WebsiteTargetChangeRequiresReplacement,
)
from app.logging_config import configure_logging
from app.monitoring import CheckAlreadyRunning, MonitorService
from app.notifications import NotificationDispatcher
from app.scheduler import MonitoringScheduler

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    settings: Settings | None = None,
    *,
    repository: Repository | None = None,
    check_runner: CheckRunner | None = None,
    notification_client: httpx.AsyncClient | None = None,
    start_background: bool = True,
) -> FastAPI:
    """Build the application; dependencies can be replaced for deterministic tests."""
    app_settings = settings or Settings.from_env()
    repo = repository or Repository(app_settings.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging(app_settings.log_level)
        repo.initialize()
        check_client: httpx.AsyncClient | None = None
        owned_check_client = check_runner is None
        if check_runner is None:
            check_client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(app_settings.timeout_seconds),
                headers={"User-Agent": f"WebsiteHealthMonitor/{__version__}"},
            )
            active_check_runner = CheckRunner(app_settings, check_client)
        else:
            active_check_runner = check_runner

        owned_notification_client = notification_client is None
        notify_client = notification_client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(app_settings.webhook_timeout_seconds),
            headers={"User-Agent": f"WebsiteHealthMonitor/{__version__}"},
        )
        monitor = MonitorService(repo, active_check_runner, app_settings)
        scheduler = MonitoringScheduler(repo, monitor, app_settings)
        dispatcher = NotificationDispatcher(repo, app_settings, notify_client)
        application.state.settings = app_settings
        application.state.repository = repo
        application.state.monitor = monitor
        application.state.scheduler = scheduler
        application.state.notification_dispatcher = dispatcher
        application.state.background_enabled = start_background
        tasks: list[asyncio.Task[None]] = []
        if start_background:
            tasks.append(asyncio.create_task(scheduler.run(), name="whm-scheduler"))
            tasks.append(asyncio.create_task(dispatcher.run(), name="whm-notifications"))
        logger.info(
            "Website Health Monitor started: version=%s background_services=%s",
            __version__,
            start_background,
        )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if owned_check_client and check_client is not None:
                await check_client.aclose()
            if owned_notification_client:
                await notify_client.aclose()
            logger.info("Website Health Monitor stopped")

    application = FastAPI(
        title="Website Health Monitor",
        description=(
            "Self-hosted monitoring for website DNS, HTTP, TLS, response time, "
            "health transitions, and incidents."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    def repository_for(request: Request) -> Repository:
        return request.app.state.repository

    def website_response(website: Any, request: Request) -> dict[str, Any]:
        repository = repository_for(request)
        return {
            **website.to_dict(),
            "health": repository.get_health(website.id),
        }

    @application.get("/healthz", tags=["operations"], summary="Liveness probe")
    def liveness() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/readyz", tags=["operations"], summary="Readiness probe")
    def readiness(request: Request) -> dict[str, Any]:
        try:
            repository_for(request).ping()
        except Exception as exc:
            logger.exception("Readiness check failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SQLite database is unavailable",
            ) from exc
        return {
            "status": "ready",
            "database": "ok",
            "background_services": bool(request.app.state.background_enabled),
        }

    @application.get("/api/health", tags=["operations"], summary="Application health details")
    def application_health(request: Request) -> dict[str, Any]:
        repository = repository_for(request)
        try:
            repository.ping()
            database_state = "ok"
        except Exception:
            logger.exception("Application health database check failed")
            database_state = "error"
        return {
            "status": "ok" if database_state == "ok" else "degraded",
            "version": __version__,
            "database": database_state,
            "background_services": bool(request.app.state.background_enabled),
            "webhook_configured": bool(request.app.state.settings.webhook_url),
            "schema_version": repository.schema_version() if database_state == "ok" else None,
        }

    @application.get("/api/dashboard", tags=["websites"], summary="Dashboard overview")
    def dashboard(request: Request) -> dict[str, Any]:
        return {"websites": repository_for(request).list_dashboard()}

    @application.get("/api/websites", tags=["websites"], summary="List configured websites")
    def list_websites(request: Request, enabled_only: bool = False) -> list[dict[str, Any]]:
        repository = repository_for(request)
        sites = repository.list_websites(enabled_only=enabled_only)
        return [website_response(site, request) for site in sites]

    @application.post(
        "/api/websites",
        tags=["websites"],
        status_code=status.HTTP_201_CREATED,
        summary="Add a website to monitor",
    )
    def create_website(payload: WebsiteCreate, request: Request) -> dict[str, Any]:
        repository = repository_for(request)
        settings = request.app.state.settings
        try:
            website = repository.create_website(
                name=payload.name,
                url=payload.url,
                enabled=payload.enabled,
                interval_seconds=payload.interval_seconds or settings.interval_seconds,
                timeout_seconds=payload.timeout_seconds or settings.timeout_seconds,
            )
        except DuplicateWebsite as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return website_response(website, request)

    @application.get("/api/websites/{website_id}", tags=["websites"], summary="Website detail")
    def website_detail(website_id: int, request: Request) -> dict[str, Any]:
        repository = repository_for(request)
        website = repository.get_website(website_id)
        if website is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
        incidents = repository.get_incidents(website_id=website_id, status="open", limit=1)
        return {
            **website_response(website, request),
            "latest_checks": repository.get_checks(website_id, limit=4),
            "active_incident": incidents[0] if incidents else None,
            "recent_transitions": repository.get_transitions(website_id, limit=10),
        }

    @application.patch("/api/websites/{website_id}", tags=["websites"], summary="Update a website")
    async def update_website(
        website_id: int, payload: WebsiteUpdate, request: Request
    ) -> dict[str, Any]:
        try:
            website = await request.app.state.monitor.update_website(website_id, payload.changes())
        except (
            DuplicateWebsite,
            WebsiteTargetChangeRequiresReplacement,
            CheckAlreadyRunning,
        ) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if website is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
        return website_response(website, request)

    @application.delete(
        "/api/websites/{website_id}",
        tags=["websites"],
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete a website and its stored history",
    )
    async def delete_website(website_id: int, request: Request) -> Response:
        try:
            deleted = await request.app.state.monitor.delete_website(website_id)
        except CheckAlreadyRunning as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.post(
        "/api/websites/{website_id}/check",
        tags=["monitoring"],
        summary="Run a monitoring cycle immediately",
    )
    async def check_website(website_id: int, request: Request) -> dict[str, Any]:
        try:
            result = await request.app.state.monitor.run_cycle(website_id)
        except WebsiteNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Website not found"
            ) from exc
        except CheckAlreadyRunning as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        assert result is not None
        return result

    @application.get(
        "/api/websites/{website_id}/checks", tags=["monitoring"], summary="Recent check results"
    )
    def website_checks(
        website_id: int,
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        repository = repository_for(request)
        if repository.get_website(website_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
        return repository.get_checks(website_id, limit=limit)

    @application.get(
        "/api/websites/{website_id}/transitions",
        tags=["monitoring"],
        summary="Recent health-state transitions",
    )
    def website_transitions(
        website_id: int,
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        repository = repository_for(request)
        if repository.get_website(website_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
        return repository.get_transitions(website_id, limit=limit)

    @application.get("/api/incidents", tags=["incidents"], summary="List incidents")
    def incidents(
        request: Request,
        status_filter: Literal["open", "resolved"] | None = Query(default=None, alias="status"),
        website_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return repository_for(request).get_incidents(
            website_id=website_id, status=status_filter, limit=limit
        )

    @application.get(
        "/api/notifications", tags=["notifications"], summary="Recent notification delivery status"
    )
    def notifications(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return repository_for(request).get_notification_events(limit=limit)

    @application.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
