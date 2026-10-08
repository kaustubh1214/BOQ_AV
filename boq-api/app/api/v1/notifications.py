import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.api.deps import DB, Ctx, PagingDep, respond, respond_page
from app.core.errors import AppError
from app.core.responses import Envelope, Message, PageEnvelope
from app.models import Notification
from app.schemas.common import Out
from app.services.common import now_utc

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(Out):
    id: uuid.UUID
    type: str
    title: str
    body: str | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime


class UnreadOut(BaseModel):
    unread: int


@router.get("", response_model=PageEnvelope[NotificationOut])
async def list_notifications(db: DB, ctx: Ctx, paging: PagingDep, unread_only: bool = False):
    stmt = select(Notification).where(Notification.user_id == ctx.user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Notification.created_at.desc()).offset(paging.offset).limit(paging.limit))).scalars().all()
    return respond_page(ctx, [NotificationOut.model_validate(n) for n in rows], total, paging)


@router.get("/unread-count", response_model=Envelope[UnreadOut])
async def unread_count(db: DB, ctx: Ctx):
    n = (
        await db.execute(
            select(func.count()).select_from(Notification).where(Notification.user_id == ctx.user_id, Notification.read_at.is_(None))
        )
    ).scalar_one()
    return respond(ctx, UnreadOut(unread=n))


@router.post("/{notification_id}/read", response_model=Envelope[Message])
async def mark_read(notification_id: uuid.UUID, db: DB, ctx: Ctx):
    res = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == ctx.user_id, Notification.read_at.is_(None))
        .values(read_at=now_utc())
    )
    if res.rowcount == 0 and await db.get(Notification, notification_id) is None:
        raise AppError("NOT_FOUND", "Notification not found", 404)
    await db.commit()
    return respond(ctx, {"message": "ok"})


@router.post("/read-all", response_model=Envelope[Message])
async def mark_all_read(db: DB, ctx: Ctx):
    await db.execute(
        update(Notification).where(Notification.user_id == ctx.user_id, Notification.read_at.is_(None)).values(read_at=now_utc())
    )
    await db.commit()
    return respond(ctx, {"message": "ok"})
