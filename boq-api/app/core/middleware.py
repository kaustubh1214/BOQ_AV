import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = logging.getLogger("boq.access")

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request-id propagation, access logging and security headers."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")

        log.info(
            "%s %s %s %.1fms rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response


def client_ip(request: Request) -> str:
    # Behind nginx we trust the first X-Forwarded-For hop (nginx overwrites it with $remote_addr).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
