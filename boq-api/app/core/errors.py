"""Machine-readable errors. Clients (React, Flutter) switch on `error.code`, never on the message."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings

log = logging.getLogger("boq.errors")


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def not_found(entity: str = "Record") -> AppError:
    return AppError("NOT_FOUND", f"{entity} not found", 404)


def forbidden(message: str = "You don't have permission to perform this action") -> AppError:
    return AppError("FORBIDDEN", message, 403)


def invalid_state(message: str, details: Any = None) -> AppError:
    return AppError("INVALID_STATE", message, 409, details)


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"success": False, "error": {"code": code, "message": message, "details": details}}


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body("VALIDATION_ERROR", "Invalid input", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        msg = str(exc.orig) if exc.orig else str(exc)
        if "duplicate key" in msg or "unique" in msg.lower():
            return JSONResponse(_body("DUPLICATE", "A record with the same unique value already exists"), status_code=409)
        if "foreign key" in msg:
            return JSONResponse(_body("INVALID_REFERENCE", "Referenced record does not exist or is in use"), status_code=409)
        if "check constraint" in msg:
            return JSONResponse(_body("CONSTRAINT_VIOLATION", "Value violates a data rule"), status_code=409)
        log.exception("integrity error")
        return JSONResponse(_body("CONFLICT", "Data conflict"), status_code=409)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        message = "Internal server error" if settings.is_production else f"{type(exc).__name__}: {exc}"
        return JSONResponse(_body("INTERNAL_ERROR", message), status_code=500)
