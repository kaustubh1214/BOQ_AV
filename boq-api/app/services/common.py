"""Small shared helpers: dates/FY, document numbers, audit, workflow guards, UOM conversion, finance scrub."""

import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import inspect, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, invalid_state
from app.models import Approval, AuditLog, Material, UomConversion
from app.services.context import UserContext
from app.services.permissions import FINANCE_FIELDS

TZ = ZoneInfo(settings.TIMEZONE)

Q3 = Decimal("0.001")
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")


def q3(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Q3, rounding=ROUND_HALF_UP)


def q4(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Q4, rounding=ROUND_HALF_UP)


def q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Q2, rounding=ROUND_HALF_UP)


def now_utc() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return datetime.now(TZ).date()


def financial_year(d: date | None = None) -> str:
    """Indian FY (April–March): 2026-10-09 → '2026-27'."""
    d = d or today()
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def fy_bounds(fy: str) -> tuple[date, date]:
    start = int(fy[:4])
    return date(start, 4, 1), date(start + 1, 3, 31)


async def next_doc_no(db: AsyncSession, doc_type: str, on: date | None = None) -> str:
    fy = financial_year(on)
    # Atomic upsert: the row lock serialises concurrent creators, so numbers never collide.
    n = (
        await db.execute(
            text(
                "INSERT INTO document_sequences (doc_type, financial_year, last_value) VALUES (:t, :fy, 1) "
                "ON CONFLICT (doc_type, financial_year) DO UPDATE "
                "SET last_value = document_sequences.last_value + 1 RETURNING last_value"
            ),
            {"t": doc_type, "fy": fy},
        )
    ).scalar_one()
    return f"{doc_type}/{fy}/{n:04d}"


# ------------------------------------------------------------------ audit


def _jsonable(v: Any) -> Any:
    if isinstance(v, (uuid.UUID, Decimal)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Enum):
        return v.value
    return v


def snapshot(obj: Any) -> dict:
    """Column values of an ORM object (no relationships), JSON-safe."""
    state = inspect(obj)
    return {attr.key: _jsonable(getattr(obj, attr.key)) for attr in state.mapper.column_attrs}


_AUDIT_SKIP = {"updated_at", "created_at", "version", "password_hash"}


def diff(before: dict | None, after: dict | None) -> tuple[dict | None, dict | None]:
    if before is None or after is None:
        strip = lambda d: {k: v for k, v in d.items() if k not in _AUDIT_SKIP} if d else d  # noqa: E731
        return strip(before), strip(after)
    keys = {k for k in after if k not in _AUDIT_SKIP and before.get(k) != after.get(k)}
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


async def audit(
    db: AsyncSession,
    ctx: UserContext | None,
    action: str,
    entity_type: str,
    entity_id: Any,
    summary: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    b, a = diff(before, after)
    db.add(
        AuditLog(
            user_id=ctx.user_id if ctx else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            summary=summary,
            before=b,
            after=a,
            ip=ctx.ip if ctx else None,
            user_agent=ctx.user_agent if ctx else None,
            request_id=ctx.request_id if ctx else None,
        )
    )


async def record_approval(
    db: AsyncSession, ctx: UserContext, entity_type: str, entity_id: uuid.UUID, stage: str, action: str, comment: str | None
) -> None:
    db.add(Approval(entity_type=entity_type, entity_id=entity_id, stage=stage, action=action, actor_id=ctx.user_id, comment=comment))


async def approvals_for(db: AsyncSession, entity_type: str, entity_id: uuid.UUID) -> list[Approval]:
    rows = await db.execute(
        select(Approval).where(Approval.entity_type == entity_type, Approval.entity_id == entity_id).order_by(Approval.acted_at)
    )
    return list(rows.scalars())


# ------------------------------------------------------------------ workflow guards


def ensure_status(doc: Any, *allowed: str, action: str = "perform this action") -> None:
    if doc.status not in allowed:
        raise invalid_state(f"Cannot {action} while document is {doc.status}", {"status": doc.status, "allowed": list(allowed)})


def check_version(doc: Any, version: int | None) -> None:
    if version is not None and version != doc.version:
        raise AppError(
            "VERSION_CONFLICT",
            "This record was changed by someone else. Reload and try again.",
            409,
            {"current_version": doc.version},
        )


def bump(doc: Any, ctx: UserContext) -> None:
    doc.version = (doc.version or 0) + 1
    if hasattr(doc, "updated_by_id"):
        doc.updated_by_id = ctx.user_id


# ------------------------------------------------------------------ UOM


async def to_base_uom(
    db: AsyncSession, material: Material, qty: Decimal, rate: Decimal | None, uom_id: uuid.UUID | None
) -> tuple[Decimal, Decimal | None]:
    """Converts a quantity (and per-unit rate) entered in another UOM into the material's base UOM."""
    if uom_id is None or uom_id == material.base_uom_id:
        return qty, rate
    rows = (
        (
            await db.execute(
                select(UomConversion).where(
                    UomConversion.is_active.is_(True),
                    or_(UomConversion.material_id == material.id, UomConversion.material_id.is_(None)),
                    or_(
                        (UomConversion.from_uom_id == uom_id) & (UomConversion.to_uom_id == material.base_uom_id),
                        (UomConversion.from_uom_id == material.base_uom_id) & (UomConversion.to_uom_id == uom_id),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise AppError("UOM_CONVERSION_MISSING", f"No conversion defined from this UOM to {material.base_uom.code} for {material.name}")
    conv = sorted(rows, key=lambda c: c.material_id is None)[0]  # material-specific beats global
    factor = conv.factor if conv.from_uom_id == uom_id else Decimal(1) / conv.factor
    return q3(qty * factor), (q4(rate / factor) if rate is not None else None)


# ------------------------------------------------------------------ finance field scrubbing


def scrub(obj: Any, show_finance: bool) -> Any:
    """Nulls rate/amount/value fields (recursively) for users without finance.view."""
    if show_finance:
        return obj
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            if name in FINANCE_FIELDS:
                setattr(obj, name, None)
            else:
                scrub(getattr(obj, name), False)
    elif isinstance(obj, dict):
        for k in list(obj):
            if k in FINANCE_FIELDS:
                obj[k] = None
            else:
                scrub(obj[k], False)
    elif isinstance(obj, list):
        for item in obj:
            scrub(item, False)
    return obj
