"""FastAPI application factory.

Run it with ``python -m app serve`` (see the README). The API only listens on the loopback
interface by default, accepts state-changing requests only from the application's own
pages, and never exposes a secret: there are none in the configuration, and the optional
Anthropic key is only ever reported as present or absent.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import ROOT, Settings, load_settings
from app.core.errors import TraderError
from app.core.security import CLIENT_HEADER, LocalGuardMiddleware
from app.monitoring.logging import get_logger
from app.services import Services

log = get_logger("api")

FRONTEND_DIST = ROOT / "frontend" / "dist"


def create_app(settings: Settings | None = None, *, services: Services | None = None,
               start_background: bool = True, frontend_dist: Path | None = FRONTEND_DIST) -> FastAPI:
    settings = settings or (services.settings if services else load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        svc = services or Services(settings, start_background=start_background)
        app.state.services = svc
        await svc.start()
        try:
            yield
        finally:
            await svc.stop()

    app = FastAPI(
        title="Claude Adaptive Trader",
        version=__version__,
        description="Paper-trading research platform. Real-money execution does not exist in this version.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(LocalGuardMiddleware, allowed_origins=settings.server.cors_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", CLIENT_HEADER],
        allow_credentials=False,
        max_age=600,
    )

    @app.exception_handler(TraderError)
    async def _trader_error(_: Request, exc: TraderError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg", "")}
                  for e in exc.errors()]
        return JSONResponse({"error": "invalid_request", "message": "the request is not valid",
                             "details": {"errors": errors}}, status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "internal_error",
                             "message": "an unexpected error occurred; the Logs page has the details",
                             "details": {}}, status_code=500)

    from app.api import system
    app.include_router(system.router)
    for module_name in _optional_routers():
        module = __import__(f"app.api.{module_name}", fromlist=["router"])
        app.include_router(module.router)

    if frontend_dist is not None and (frontend_dist / "index.html").exists():
        _mount_frontend(app, frontend_dist)
    return app


def _optional_routers() -> list[str]:
    """Routers are added stage by stage; each lives in its own module under app/api."""
    here = Path(__file__).parent / "api"
    names = ["market", "data", "strategies", "research", "backtests", "paper", "portfolio", "risk",
             "analytics", "logs", "dashboard", "stream"]
    return [n for n in names if (here / f"{n}.py").exists()]


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str) -> FileResponse:
        if path.startswith(("api/", "ws/")):
            return JSONResponse({"error": "not_found", "message": "no such endpoint", "details": {}},
                                status_code=404)  # type: ignore[return-value]
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
