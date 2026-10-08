"""Attachments (invoice / challan photos, signed gate passes). Stored on local disk behind a small interface;
swap `_store` / `_open` for S3 when moving to cloud storage. Access is checked against the parent document."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, Ctx, respond
from app.core.config import settings
from app.core.errors import AppError
from app.core.responses import Envelope, Message
from app.models import Attachment, Boq, Grn, IssueNote, MarketRate, MaterialRequest, ReturnNote, StockAdjustment
from app.services.common import audit
from app.services.context import UserContext
from app.services.names import user_names

router = APIRouter(prefix="/attachments", tags=["attachments"])

_MAGIC = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "application/pdf": [b"%PDF-"],
    "image/webp": [b"RIFF"],
}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf", "image/webp": ".webp"}

# entity_type → (model, read check, write permissions)
_ENTITIES = {
    "grn": (Grn, lambda ctx, o, p: ctx.covers(p, store_ids=(o.store_id,)), ("grn.read",), ("grn.create", "grn.post")),
    "issue_note": (
        IssueNote,
        lambda ctx, o, p: ctx.covers(p, site_id=o.site_id, project_id=o.project_id, store_ids=(o.from_store_id, o.to_store_id)),
        ("issue.read",),
        ("issue.create", "issue.post", "issue.receive"),
    ),
    "rfq": (
        MaterialRequest,
        lambda ctx, o, p: ctx.covers(p, site_id=o.site_id, project_id=o.project_id, store_ids=(o.store_id,)),
        ("rfq.read",),
        ("rfq.create", "rfq.approve_pm", "rfq.approve_store"),
    ),
    "return": (
        ReturnNote,
        lambda ctx, o, p: ctx.covers(p, site_id=o.site_id, project_id=o.project_id, store_ids=(o.from_store_id, o.to_store_id)),
        ("site.read", "return.receive"),
        ("site.return", "return.receive"),
    ),
    "adjustment": (
        StockAdjustment,
        lambda ctx, o, p: ctx.covers(p, store_ids=(o.store_id,)),
        ("stock.read",),
        ("adjustment.create", "adjustment.approve"),
    ),
    "boq": (Boq, lambda ctx, o, p: ctx.has(p), ("boq.read",), ("boq.create", "boq.approve")),
    "market_rate": (MarketRate, lambda ctx, o, p: ctx.has(p), ("market.read",), ("market.manage",)),
}


class AttachmentOut(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    filename: str
    mime: str
    size: int
    uploaded_by_name: str | None
    created_at: datetime


async def _check(db, ctx: UserContext, entity_type: str, entity_id: uuid.UUID, write: bool):
    spec = _ENTITIES.get(entity_type)
    if spec is None:
        raise AppError("VALIDATION_ERROR", f"Unsupported entity_type '{entity_type}'", 422)
    model, covers, read_perms, write_perms = spec
    obj = await db.get(model, entity_id)
    if obj is None or not any(covers(ctx, obj, p) for p in read_perms):
        raise AppError("NOT_FOUND", "Document not found", 404)
    if write and not any(covers(ctx, obj, p) for p in write_perms):
        raise AppError("FORBIDDEN", "You can't attach files to this document", 403)


def _root() -> Path:
    root = Path(settings.UPLOAD_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post("", response_model=Envelope[AttachmentOut], status_code=201)
async def upload(
    db: DB,
    ctx: Ctx,
    entity_type: Annotated[str, Form()],
    entity_id: Annotated[uuid.UUID, Form()],
    file: UploadFile = File(...),
):
    await _check(db, ctx, entity_type, entity_id, write=True)
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError("PAYLOAD_TOO_LARGE", f"File must be under {settings.MAX_UPLOAD_MB} MB", 413)
    # Trust the bytes, not the client-declared content type.
    mime = next((m for m, sigs in _MAGIC.items() if any(data.startswith(s) for s in sigs)), None)
    if mime == "image/webp" and data[8:12] != b"WEBP":
        mime = None
    if mime is None:
        raise AppError("UNSUPPORTED_FILE", "Only JPEG, PNG, WEBP images and PDF files are allowed", 415)
    now = datetime.now()
    att_id = uuid.uuid4()
    key = f"{now:%Y/%m}/{att_id.hex}{_EXT[mime]}"
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    safe_name = Path(file.filename or f"file{_EXT[mime]}").name[:200]
    att = Attachment(
        id=att_id,
        entity_type=entity_type,
        entity_id=entity_id,
        storage_key=key,
        filename=safe_name,
        mime=mime,
        size=len(data),
        uploaded_by_id=ctx.user_id,
    )
    db.add(att)
    await audit(db, ctx, "UPLOAD", entity_type, entity_id, f"Attached {safe_name}")
    await db.commit()
    return respond(
        ctx,
        AttachmentOut(
            id=att.id,
            entity_type=entity_type,
            entity_id=entity_id,
            filename=safe_name,
            mime=mime,
            size=len(data),
            uploaded_by_name=ctx.user.name,
            created_at=now,
        ),
    )


@router.get("", response_model=Envelope[list[AttachmentOut]])
async def list_attachments(db: DB, ctx: Ctx, entity_type: str, entity_id: uuid.UUID):
    await _check(db, ctx, entity_type, entity_id, write=False)
    rows = (
        (
            await db.execute(
                select(Attachment)
                .where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
                .order_by(Attachment.created_at)
            )
        )
        .scalars()
        .all()
    )
    names = await user_names(db, [a.uploaded_by_id for a in rows])
    return respond(
        ctx,
        [
            AttachmentOut(
                id=a.id,
                entity_type=a.entity_type,
                entity_id=a.entity_id,
                filename=a.filename,
                mime=a.mime,
                size=a.size,
                uploaded_by_name=names.get(a.uploaded_by_id),
                created_at=a.created_at,
            )
            for a in rows
        ],
    )


@router.get("/{attachment_id}/download", response_class=FileResponse)
async def download(attachment_id: uuid.UUID, db: DB, ctx: Ctx):
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise AppError("NOT_FOUND", "Attachment not found", 404)
    await _check(db, ctx, att.entity_type, att.entity_id, write=False)
    path = (_root() / att.storage_key).resolve()
    if not str(path).startswith(str(_root())) or not path.exists():
        raise AppError("NOT_FOUND", "File missing", 404)
    return FileResponse(path, media_type=att.mime, filename=att.filename, content_disposition_type="inline")


@router.delete("/{attachment_id}", response_model=Envelope[Message])
async def delete_attachment(attachment_id: uuid.UUID, db: DB, ctx: Ctx):
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise AppError("NOT_FOUND", "Attachment not found", 404)
    await _check(db, ctx, att.entity_type, att.entity_id, write=True)
    if att.uploaded_by_id != ctx.user_id:
        raise AppError("FORBIDDEN", "Only the uploader can remove an attachment", 403)
    await db.delete(att)
    await audit(db, ctx, "DELETE_ATTACHMENT", att.entity_type, att.entity_id, att.filename)
    await db.commit()
    # The file stays on disk (audit trail); a housekeeping job can purge orphans.
    return respond(ctx, {"message": "removed"})
