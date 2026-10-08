import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import (
    admin,
    attachments,
    auth,
    boq,
    dashboard,
    grn,
    issue,
    market,
    masters,
    me,
    meta,
    notifications,
    reports,
    rfq,
    site,
    stock,
)
from app.core.config import settings
from app.core.database import engine
from app.core.errors import register_error_handlers
from app.core.middleware import RequestContextMiddleware
from app.core.redis import close_redis, get_redis, init_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_redis()
    yield
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title=f"{settings.COMPANY_NAME} — BOQ & Material Management API",
    version="1.0.0",
    description=(
        "Central store BOQ, inward (GRN), site material requests (RFQ), issue notes, site consumption, "
        "stock ledger and reports. Versioned under /api/v1; shared by the React web app and the Flutter app."
    ),
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID", "If-Match"],
    expose_headers=["X-Request-ID", "Content-Disposition"],
)
register_error_handlers(app)

api = APIRouter(prefix="/api/v1")
for module in (auth, me, admin, meta, masters, market, boq, grn, rfq, issue, site, stock, reports, dashboard, notifications, attachments):
    api.include_router(module.router)
app.include_router(api)


@app.get("/health", tags=["ops"], summary="Liveness")
async def health():
    return {"status": "ok"}


@app.get("/ready", tags=["ops"], summary="Readiness: database (and Redis if configured)")
async def ready():
    checks = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover
        checks["database"] = f"error: {type(exc).__name__}"
    checks["redis"] = "ok" if get_redis() is not None else ("disabled" if not settings.REDIS_URL else "unavailable")
    ok = checks["database"] == "ok"
    from fastapi.responses import JSONResponse

    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)
