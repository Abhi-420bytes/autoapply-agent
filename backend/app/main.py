from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    analytics,
    email,
    health,
    jobs,
    knowledge,
    llm,
    notifications,
    outreach,
    portals,
    settings,
    sites,
    templates,
)
from app.api.deps import require_api_token
from app.core.config import get_config
from app.security.redaction import configure_logging
from app.services.llm_admin import AdminError


def create_app() -> FastAPI:
    cfg = get_config()
    configure_logging(cfg.log_level)

    app = FastAPI(title="AutoApply Agent", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(health.router)  # unauthenticated: used by Docker healthchecks
    protected = [Depends(require_api_token)]
    app.include_router(settings.router, dependencies=protected)
    app.include_router(llm.router, dependencies=protected)
    app.include_router(notifications.router, dependencies=protected)
    app.include_router(templates.router, dependencies=protected)
    app.include_router(knowledge.router, dependencies=protected)
    app.include_router(jobs.router, dependencies=protected)
    app.include_router(email.router, dependencies=protected)
    app.include_router(portals.router, dependencies=protected)
    app.include_router(analytics.router, dependencies=protected)
    app.include_router(sites.router, dependencies=protected)
    app.include_router(outreach.router, dependencies=protected)

    @app.exception_handler(AdminError)
    async def _admin_error(_: Request, exc: AdminError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    return app


app = create_app()
