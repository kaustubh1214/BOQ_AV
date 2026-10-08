"""Authentication for both clients.

Web:     POST /auth/login   → access token in body, refresh token in an httpOnly SameSite=Strict cookie.
Flutter: POST /auth/token   → access + refresh token in body (store in flutter_secure_storage).
Both:    POST /auth/refresh → rotates the refresh token (cookie or body). Re-use of an old token revokes the family.
"""

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import EmailStr, Field
from sqlalchemy import func, select, update

from app.api.deps import DB, Ctx, get_ctx_allow_password_change
from app.core.config import settings
from app.core.errors import AppError
from app.core.middleware import client_ip
from app.core.redis import hit, reset
from app.core.responses import Envelope, Message
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.models import User, UserSession
from app.models.enums import Platform
from app.schemas.common import In, Out
from app.services.common import audit, now_utc
from app.services.context import UserContext

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(In):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=200)]


class TokenIn(LoginIn):
    device_name: Annotated[str | None, Field(max_length=120)] = None
    platform: Platform = Platform.ANDROID


class RefreshIn(In):
    refresh_token: str | None = None


class ChangePasswordIn(In):
    current_password: Annotated[str, Field(max_length=200)]
    new_password: Annotated[str, Field(max_length=200)]


class TokenOut(Out):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None
    must_change_password: bool = False


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        token,
        max_age=settings.REFRESH_TOKEN_DAYS * 86400,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="strict",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(settings.REFRESH_COOKIE_NAME, path="/api/v1/auth")


async def _authenticate(db, request: Request, email: str, password: str) -> User:
    email = email.lower().strip()
    ip = client_ip(request)
    rl_key = f"rl:login:{ip}:{email}"
    if await hit(rl_key, 60) > settings.LOGIN_RATE_LIMIT_PER_MIN:
        raise AppError("RATE_LIMITED", "Too many login attempts. Try again in a minute.", 429)

    user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    now = now_utc()
    if user is not None and user.locked_until and user.locked_until > now:
        raise AppError("ACCOUNT_LOCKED", "Account temporarily locked after repeated failures. Try later.", 423)

    if not verify_password(password, user.password_hash if user else None) or user is None or not user.is_active:
        if user is not None:
            user.failed_attempts += 1
            if user.failed_attempts >= settings.LOCKOUT_THRESHOLD:
                user.locked_until = now + timedelta(minutes=settings.LOCKOUT_MINUTES)
                user.failed_attempts = 0
            await audit(db, None, "LOGIN_FAILED", "user", user.id, f"Failed login from {ip}")
            await db.commit()
        raise AppError("INVALID_CREDENTIALS", "Invalid email or password", 401)

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    await reset(rl_key)
    return user


async def _issue_session(db, request: Request, user: User, platform: str, device_name: str | None, family_id=None):
    refresh = new_refresh_token()
    session = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(refresh),
        platform=platform,
        device_name=device_name,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
        expires_at=now_utc() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
    )
    db.add(session)
    access, ttl = create_access_token(user.id, user.token_version, session.family_id)
    return access, ttl, refresh


@router.post("/login", response_model=Envelope[TokenOut], summary="Web login (refresh token set as httpOnly cookie)")
async def login(body: LoginIn, request: Request, response: Response, db: DB):
    user = await _authenticate(db, request, body.email, body.password)
    access, ttl, refresh = await _issue_session(db, request, user, Platform.WEB, "browser")
    await audit(db, None, "LOGIN", "user", user.id, "Web login")
    await db.commit()
    _set_refresh_cookie(response, refresh)
    return {
        "success": True,
        "data": TokenOut(access_token=access, expires_in=ttl, must_change_password=user.must_change_password).model_dump(),
        "meta": {},
    }


@router.post("/token", response_model=Envelope[TokenOut], summary="Mobile login (tokens in body)")
async def token(body: TokenIn, request: Request, db: DB):
    user = await _authenticate(db, request, body.email, body.password)
    access, ttl, refresh = await _issue_session(db, request, user, body.platform, body.device_name)
    await audit(db, None, "LOGIN", "user", user.id, f"Mobile login ({body.platform})")
    await db.commit()
    return {
        "success": True,
        "data": TokenOut(
            access_token=access, expires_in=ttl, refresh_token=refresh, must_change_password=user.must_change_password
        ).model_dump(),
        "meta": {},
    }


@router.post("/refresh", response_model=Envelope[TokenOut])
async def refresh(
    request: Request,
    response: Response,
    db: DB,
    body: RefreshIn | None = None,
    cookie_token: Annotated[str | None, Cookie(alias=settings.REFRESH_COOKIE_NAME)] = None,
):
    presented = (body.refresh_token if body and body.refresh_token else None) or cookie_token
    from_cookie = not (body and body.refresh_token)
    if not presented:
        raise AppError("UNAUTHORIZED", "No refresh token", 401)

    session = (
        await db.execute(select(UserSession).where(UserSession.token_hash == hash_token(presented)).with_for_update())
    ).scalar_one_or_none()
    now = now_utc()
    if session is None:
        raise AppError("UNAUTHORIZED", "Invalid refresh token", 401)
    if session.rotated_at is not None or session.revoked_at is not None:
        # Token re-use: someone is replaying an old token → kill the whole family.
        await db.execute(
            update(UserSession).where(UserSession.family_id == session.family_id, UserSession.revoked_at.is_(None)).values(revoked_at=now)
        )
        await audit(db, None, "REFRESH_REUSE", "user", session.user_id, "Refresh token reuse detected; family revoked")
        await db.commit()
        if from_cookie:
            _clear_refresh_cookie(response)
        raise AppError("UNAUTHORIZED", "Session revoked", 401)
    if session.expires_at < now:
        raise AppError("UNAUTHORIZED", "Refresh token expired", 401)

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise AppError("UNAUTHORIZED", "Account disabled", 401)

    session.rotated_at = now
    access, ttl, new_refresh = await _issue_session(db, request, user, session.platform, session.device_name, family_id=session.family_id)
    await db.commit()
    data = TokenOut(access_token=access, expires_in=ttl, must_change_password=user.must_change_password)
    if from_cookie:
        _set_refresh_cookie(response, new_refresh)
    else:
        data.refresh_token = new_refresh
    return {"success": True, "data": data.model_dump(), "meta": {}}


@router.post("/logout", response_model=Envelope[Message])
async def logout(
    response: Response,
    db: DB,
    body: RefreshIn | None = None,
    cookie_token: Annotated[str | None, Cookie(alias=settings.REFRESH_COOKIE_NAME)] = None,
):
    presented = (body.refresh_token if body and body.refresh_token else None) or cookie_token
    if presented:
        session = (await db.execute(select(UserSession).where(UserSession.token_hash == hash_token(presented)))).scalar_one_or_none()
        if session is not None:
            await db.execute(
                update(UserSession)
                .where(UserSession.family_id == session.family_id, UserSession.revoked_at.is_(None))
                .values(revoked_at=now_utc())
            )
            await db.commit()
    _clear_refresh_cookie(response)
    return {"success": True, "data": {"message": "Logged out"}, "meta": {}}


@router.post("/logout-all", response_model=Envelope[Message], summary="Revoke every session and outstanding access token")
async def logout_all(response: Response, db: DB, ctx: Ctx):
    user = await db.get(User, ctx.user_id)
    user.token_version += 1
    await db.execute(
        update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=now_utc())
    )
    await audit(db, ctx, "LOGOUT_ALL", "user", user.id)
    await db.commit()
    _clear_refresh_cookie(response)
    return {"success": True, "data": {"message": "Logged out of all devices"}, "meta": {}}


@router.post("/change-password", response_model=Envelope[TokenOut])
async def change_password(
    body: ChangePasswordIn,
    request: Request,
    response: Response,
    db: DB,
    ctx: Annotated[UserContext, Depends(get_ctx_allow_password_change)],
):
    user = await db.get(User, ctx.user_id)
    if not verify_password(body.current_password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Current password is incorrect", 400)
    if body.new_password == body.current_password:
        raise AppError("VALIDATION_ERROR", "New password must differ from the current one", 422)
    if err := validate_password_strength(body.new_password):
        raise AppError("WEAK_PASSWORD", err, 422)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1  # invalidates every other access token
    await db.execute(
        update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=now_utc())
    )
    access, ttl, refresh_tok = await _issue_session(db, request, user, Platform.WEB, "browser")
    await audit(db, ctx, "PASSWORD_CHANGED", "user", user.id)
    await db.commit()
    data = TokenOut(access_token=access, expires_in=ttl)
    if settings.REFRESH_COOKIE_NAME in request.cookies:
        _set_refresh_cookie(response, refresh_tok)  # browser client
    else:
        data.refresh_token = refresh_tok  # mobile client
    return {"success": True, "data": data.model_dump(), "meta": {}}
