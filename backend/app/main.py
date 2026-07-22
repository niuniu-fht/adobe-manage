import hashlib
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .alerts import seed_alert_rules
from .api import api_router, auth_router
from .config import BASE_DIR, settings
from .database import SessionLocal, create_schema, migrate_schema
from .polling import fleet_poller
from .remote import remote_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.auto_migrate:
        migrate_schema()
    else:
        create_schema()
    with SessionLocal() as db:
        seed_alert_rules(db)
    fleet_poller.start()
    try:
        yield
    finally:
        await fleet_poller.stop()
        await remote_client.close()


app = FastAPI(title="Adobe2API Manager", version="1.0.0", lifespan=lifespan)
session_secret = hashlib.sha256(
    f"adobe2api-manager:{settings.access_key or 'configure-access-key'}".encode()
).hexdigest()
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="adobe2api_manager_session",
    max_age=43200,
    same_site="strict",
    https_only=settings.cookie_secure,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = str(request.headers.get("x-request-id") or uuid.uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Response-Time"] = f"{time.perf_counter() - started:.4f}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "access_key_configured": bool(settings.access_key),
        "ops_key_configured": bool(settings.ops_key),
    }


app.include_router(auth_router)
app.include_router(api_router)

frontend_dist = BASE_DIR / "frontend" / "dist"
assets_dir = frontend_dist / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    index = frontend_dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        status_code=503,
        content={"detail": "Frontend is not built. Run npm run build in frontend."},
    )
