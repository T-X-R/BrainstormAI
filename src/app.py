"""FastAPI application assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.config.logging import setup_logging
from src.config.settings import get_settings
from src.infra.db.engine import init_db, close_db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle."""
    settings = get_settings()
    setup_logging(debug=settings.app.debug)
    logger.info("Starting {} v0.1.0", settings.app.name)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    yield

    # Shutdown
    await close_db()
    logger.info("Application shut down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app.name,
        version="0.1.0",
        description="AI Group Chat Brainstorming",
        lifespan=lifespan,
    )

    # Register routes
    from src.api.http import router as http_router
    from src.api.ws import router as ws_router

    app.include_router(http_router, prefix="/api")
    app.include_router(ws_router)

    # Serve static files
    static_dir = _PROJECT_ROOT / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        logger.info("Serving static files from {}", static_dir)

    # Root redirect to static index
    from fastapi.responses import RedirectResponse

    @app.get("/")
    async def root():
        return RedirectResponse(url="/static/index.html")

    return app


app = create_app()
