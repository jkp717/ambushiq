"""AmbushIQ API — app entry point, middleware & router inclusion."""
from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

from app.bulk.router import router as bulk_router
from app.cameras.router import router as cameras_router
from app.core.config import APP_TOKEN, APP_VERSION
from app.core.database import init_db
from app.corridors.router import router as corridors_router
from app.deer_ratings.router import router as deer_ratings_router
from app.deer_sign.router import router as deer_sign_router
from app.dependencies import require_token
from app.forecast.router import router as forecast_router
from app.forecast.service import ForecastUnavailable
from app.regions.router import router as regions_router
from app.scheduler import start_scheduler
from app.scouting.router import router as scouting_router
from app.settings.router import router as settings_router
from app.stands.router import router as stands_router
from app.zones.router import router as zones_router

app = FastAPI(title="AmbushIQ")


@app.middleware("http")
async def _static_cache_headers(request: Request, call_next):
    """Vite hashes /assets filenames per build, so those can cache forever —
    but index.html (which references those hashed names) must always be
    revalidated, or browsers/phones keep serving a stale shell that points at
    asset URLs from an old deploy and UI updates never show up."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/") and not path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(ForecastUnavailable)
async def _forecast_unavailable(request: Request, exc: ForecastUnavailable):
    """A weather-provider outage is a bad gateway, not a crash: return a clean 502 (which the
    frontend already handles) instead of an unhandled ASGI exception and a 500."""
    return JSONResponse(status_code=502, content={"detail": f"forecast unreachable: {exc}"})


@app.on_event("startup")
def _startup():
    init_db()
    try:
        start_scheduler()
    except Exception:
        pass  # scheduler is best-effort; app must boot regardless


@app.get("/api/health")
def health():
    return {"ok": True, "auth_required": bool(APP_TOKEN and APP_TOKEN != "unused"), "version": APP_VERSION}


@app.get("/api/verify")
def verify(_=Depends(require_token)):
    return {"ok": True}


app.include_router(stands_router)
app.include_router(zones_router)
app.include_router(corridors_router)
app.include_router(deer_sign_router)
app.include_router(forecast_router)
app.include_router(deer_ratings_router)
app.include_router(settings_router)
app.include_router(cameras_router)
app.include_router(scouting_router)
app.include_router(bulk_router)
app.include_router(regions_router)


# ---------- static frontend ----------
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/{path:path}")
    def spa(path: str):
        candidate = os.path.join(STATIC_DIR, path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
