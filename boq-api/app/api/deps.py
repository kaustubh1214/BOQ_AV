import uuid
from collections.abc import Callable
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import AppError, forbidden
from app.core.middleware import client_ip
from app.core.security import decode_access_token
from app.models import IdempotencyKey, User
from app.services.common import scrub
from app.services.context import UserContext, load_context

DB = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


async def _resolve_ctx(request: Request, db: AsyncSession, creds: HTTPAuthorizationCredentials | None) -> UserContext:
    if creds is None or creds.scheme.lower() != "bearer":
        raise AppError("UNAUTHORIZED", "Not authenticated", 401)
    try:
        payload = decode_access_token(creds.credentials)
        user_id = uuid.UUID(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise AppError("TOKEN_EXPIRED", "Access token expired", 401)
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise AppError("UNAUTHORIZED", "Invalid token", 401)

    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != payload.get("tv"):
        raise AppError("UNAUTHORIZED", "Session is no longer valid", 401)
    return await load_context(
        db,
        user,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
        request_id=getattr(request.state, "request_id", None),
    )


async def get_ctx_allow_password_change(
    request: Request, db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> UserContext:
    return await _resolve_ctx(request, db, creds)


async def get_ctx(
    ctx: Annotated[UserContext, Depends(get_ctx_allow_password_change)],
) -> UserContext:
    if ctx.user.must_change_password:
        raise AppError("PASSWORD_CHANGE_REQUIRED", "Change your password to continue", 403)
    return ctx


Ctx = Annotated[UserContext, Depends(get_ctx)]


def require(*perms: str) -> Callable:
    """Route-level check: the caller must hold at least one of `perms` (in some scope)."""

    async def _check(ctx: Ctx) -> UserContext:
        if not any(ctx.has(p) for p in perms):
            raise forbidden(f"Missing permission: {' or '.join(perms)}")
        return ctx

    return _check


def Perm(*perms: str) -> Any:  # noqa: N802 - reads like a type in signatures
    return Annotated[UserContext, Depends(require(*perms))]


class Paging:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1)] = 1,
        limit: Annotated[int, Query(ge=1, le=200)] = 25,
    ):
        self.page = page
        self.limit = limit
        self.offset = (page - 1) * limit


PagingDep = Annotated[Paging, Depends()]


class Idempotency:
    """Optional `Idempotency-Key` header on create endpoints. Replays the first response for retries."""

    def __init__(self, request: Request, db: DB, ctx: Ctx, key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        self.key = key[:80] if key else None
        self.db = db
        self.ctx = ctx
        self.endpoint = f"{request.method} {request.url.path}"[:120]

    async def replay(self) -> dict | None:
        if not self.key:
            return None
        row = await self.db.get(IdempotencyKey, (self.ctx.user_id, self.key))
        if row is None:
            return None
        if row.endpoint != self.endpoint:
            raise AppError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key already used for a different request", 422)
        return row.response

    async def store(self, response: dict, status_code: int = 200) -> None:
        if self.key:
            self.db.add(
                IdempotencyKey(user_id=self.ctx.user_id, key=self.key, endpoint=self.endpoint, status_code=status_code, response=response)
            )


IdemDep = Annotated[Idempotency, Depends()]


def respond(ctx: UserContext, data: Any, meta: dict | None = None) -> dict:
    return {"success": True, "data": scrub(data, ctx.show_finance), "meta": meta or {}}


def respond_page(ctx: UserContext, data: list, total: int, paging: Paging) -> dict:
    import math

    return {
        "success": True,
        "data": scrub(data, ctx.show_finance),
        "meta": {
            "page": paging.page,
            "limit": paging.limit,
            "total": total,
            "totalPages": max(1, math.ceil(total / paging.limit)),
        },
    }


async def get_or_404(db: AsyncSession, model: type, id_: uuid.UUID, label: str | None = None, *, lock: bool = False, options=()):
    stmt = select(model).where(model.id == id_).options(*options)
    if lock:
        stmt = stmt.with_for_update(of=model)
    obj = (await db.execute(stmt.execution_options(populate_existing=True))).unique().scalar_one_or_none()
    if obj is None:
        raise AppError("NOT_FOUND", f"{label or model.__name__} not found", 404)
    return obj


async def finish(db: AsyncSession, ctx: UserContext, loader: Callable, idem: "Idempotency | None" = None) -> dict:
    """Flush, build the response (loaders re-read with populate_existing, still inside the transaction, so a
    failure here rolls everything back), remember it for idempotent replays, then commit."""
    await db.flush()
    payload = respond(ctx, await loader())
    if idem is not None and idem.key:
        await idem.store(jsonable_encoder(payload))
    await db.commit()
    return payload
