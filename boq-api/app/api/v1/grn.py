"""Inward / Goods Receipt Note. Posting adds accepted quantity to the central store at the GRN rate
(weighted average) and counts against the BOQ ("10,000 inward → 90,000 pending")."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select

from app.api.deps import DB, IdemDep, PagingDep, Perm, finish, get_or_404, respond, respond_page
from app.core.config import settings
from app.core.errors import AppError
from app.core.responses import Envelope, PageEnvelope
from app.models import Boq, Grn, GrnItem, Store, Vendor
from app.models.enums import BoqStatus, DocType, GrnStatus, StoreType, TxnType
from app.schemas.common import CancelIn, In, NonNegQty, Num, OptNum, Out, PosQty, Rate, VersionIn
from app.schemas.docs import MaterialBrief, StoreBrief, mat_brief
from app.services.boq import BOQ_FULL, approved_boq_for_fy, inward_by_material
from app.services.common import (
    audit,
    bump,
    check_version,
    ensure_status,
    financial_year,
    maker_checker,
    next_doc_no,
    now_utc,
    q2,
    q3,
    snapshot,
    to_base_uom,
    today,
)
from app.services.context import UserContext, ensure_covers, scope_filter
from app.services.lookups import central_store, ensure_unique_materials, materials_by_id
from app.services.names import user_names
from app.services.notify import notify
from app.services.stock_engine import Movement, post_movements

router = APIRouter(prefix="/grns", tags=["inward (GRN)"])


class GrnItemIn(In):
    material_id: uuid.UUID
    received_qty: PosQty
    rejected_qty: NonNegQty = Decimal("0")
    rate: Rate
    gst_rate: Annotated[Decimal | None, Field(ge=0, le=100)] = None
    uom_id: uuid.UUID | None = None
    rejection_reason: Annotated[str | None, Field(max_length=200)] = None

    @model_validator(mode="after")
    def _rejected_le_received(self):
        if self.rejected_qty > self.received_qty:
            raise ValueError("rejected_qty cannot exceed received_qty")
        return self


class GrnIn(In):
    vendor_id: uuid.UUID
    boq_id: uuid.UUID | None = Field(None, description="Defaults to the approved BOQ of the receipt's financial year")
    store_id: uuid.UUID | None = Field(None, description="Defaults to the central store")
    received_date: date | None = None
    invoice_no: Annotated[str | None, Field(max_length=40)] = None
    invoice_date: date | None = None
    challan_no: Annotated[str | None, Field(max_length=40)] = None
    vehicle_no: Annotated[str | None, Field(max_length=20)] = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[GrnItemIn], Field(min_length=1, max_length=500)]


class GrnUpdateIn(GrnIn):
    version: int


class GrnItemOut(Out):
    id: uuid.UUID
    line_no: int
    material: MaterialBrief
    received_qty: Num
    rejected_qty: Num
    accepted_qty: Num
    rate: OptNum
    gst_rate: Num
    amount: OptNum
    gst_amount: OptNum
    rejection_reason: str | None


class GrnSummaryOut(Out):
    id: uuid.UUID
    grn_no: str
    status: str
    version: int
    store: StoreBrief
    vendor_id: uuid.UUID
    vendor_name: str
    boq_id: uuid.UUID
    boq_no: str
    received_date: date
    invoice_no: str | None
    invoice_date: date | None
    challan_no: str | None
    vehicle_no: str | None
    remarks: str | None
    total_amount: OptNum
    total_gst: OptNum
    item_count: int
    created_by_id: uuid.UUID | None
    created_by_name: str | None = None
    posted_by_id: uuid.UUID | None
    posted_by_name: str | None = None
    posted_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime


class GrnOut(GrnSummaryOut):
    items: list[GrnItemOut]


def _summary(g: Grn, names: dict) -> GrnSummaryOut:
    return GrnSummaryOut(
        id=g.id,
        grn_no=g.grn_no,
        status=g.status,
        version=g.version,
        store=StoreBrief(id=g.store.id, code=g.store.code, name=g.store.name),
        vendor_id=g.vendor_id,
        vendor_name=g.vendor.name,
        boq_id=g.boq_id,
        boq_no=g.boq.boq_no,
        received_date=g.received_date,
        invoice_no=g.invoice_no,
        invoice_date=g.invoice_date,
        challan_no=g.challan_no,
        vehicle_no=g.vehicle_no,
        remarks=g.remarks,
        total_amount=g.total_amount,
        total_gst=g.total_gst,
        item_count=len(g.items),
        created_by_id=g.created_by_id,
        created_by_name=names.get(g.created_by_id),
        posted_by_id=g.posted_by_id,
        posted_by_name=names.get(g.posted_by_id),
        posted_at=g.posted_at,
        cancelled_at=g.cancelled_at,
        cancel_reason=g.cancel_reason,
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


async def _full(db, grn_id: uuid.UUID) -> GrnOut:
    g = await get_or_404(db, Grn, grn_id, "GRN")
    names = await user_names(db, [g.created_by_id, g.posted_by_id])
    return GrnOut(
        **_summary(g, names).model_dump(),
        items=[
            GrnItemOut(
                id=i.id,
                line_no=i.line_no,
                material=mat_brief(i.material),
                received_qty=i.received_qty,
                rejected_qty=i.rejected_qty,
                accepted_qty=i.accepted_qty,
                rate=i.rate,
                gst_rate=i.gst_rate,
                amount=i.amount,
                gst_amount=i.gst_amount,
                rejection_reason=i.rejection_reason,
            )
            for i in g.items
        ],
    )


async def _load_for(db, ctx: UserContext, grn_id: uuid.UUID, perm: str, lock: bool = False) -> Grn:
    g = await get_or_404(db, Grn, grn_id, "GRN", lock=lock)
    ensure_covers(ctx, perm, "GRN", store_ids=(g.store_id,))
    return g


async def _resolve_header(db, ctx: UserContext, body: GrnIn) -> tuple[Store, Vendor, Boq]:
    store = await db.get(Store, body.store_id) if body.store_id else await central_store(db)
    if store is None or store.type != StoreType.CENTRAL or not store.is_active:
        raise AppError("INVALID_REFERENCE", "GRNs are received into an active central store", 422)
    ensure_covers(ctx, "grn.create", "Store", store_ids=(store.id,))
    vendor = await db.get(Vendor, body.vendor_id)
    if vendor is None or not vendor.is_active:
        raise AppError("INVALID_REFERENCE", "Vendor not found or inactive", 422)
    if body.boq_id:
        boq = await get_or_404(db, Boq, body.boq_id, "BOQ", options=BOQ_FULL)
    else:
        boq = await approved_boq_for_fy(db, financial_year(body.received_date or today()))
        if boq is None:
            raise AppError("NO_APPROVED_BOQ", "There is no approved BOQ for this financial year", 409)
    if boq.status != BoqStatus.APPROVED:
        raise AppError("BOQ_NOT_APPROVED", f"BOQ {boq.boq_no} is {boq.status}; receive against the approved BOQ", 409)
    return store, vendor, boq


async def _build_items(db, body: GrnIn, boq: Boq) -> list[GrnItem]:
    ensure_unique_materials(body.items)
    mats = await materials_by_id(db, [i.material_id for i in body.items])
    in_boq = {it.material_id for it in boq.items}
    not_planned = [mats[i.material_id].name for i in body.items if i.material_id not in in_boq]
    if not_planned:
        raise AppError("MATERIAL_NOT_IN_BOQ", f"Not in BOQ {boq.boq_no}: {', '.join(not_planned)}. Revise the BOQ first.", 422)
    out = []
    for n, i in enumerate(body.items, start=1):
        m = mats[i.material_id]
        received, rate = await to_base_uom(db, m, i.received_qty, i.rate, i.uom_id)
        rejected, _ = await to_base_uom(db, m, i.rejected_qty, None, i.uom_id)
        accepted = q3(received - rejected)
        amount = q2(accepted * rate)
        gst_rate = i.gst_rate if i.gst_rate is not None else m.gst_rate
        out.append(
            GrnItem(
                id=uuid.uuid4(),
                line_no=n,
                material_id=m.id,
                received_qty=received,
                rejected_qty=rejected,
                accepted_qty=accepted,
                rate=rate,
                gst_rate=gst_rate,
                amount=amount,
                gst_amount=q2(amount * gst_rate / 100),
                rejection_reason=i.rejection_reason,
            )
        )
    return out


def _totals(g: Grn) -> None:
    g.total_amount = q2(sum((i.amount for i in g.items), Decimal(0)))
    g.total_gst = q2(sum((i.gst_amount for i in g.items), Decimal(0)))


# ------------------------------------------------------------------ endpoints


@router.get("", response_model=PageEnvelope[GrnSummaryOut])
async def list_grns(
    db: DB,
    ctx: Perm("grn.read"),
    paging: PagingDep,
    status: GrnStatus | None = None,
    vendor_id: uuid.UUID | None = None,
    boq_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
):
    stmt = select(Grn).where(scope_filter(ctx.scope("grn.read"), store_cols=(Grn.store_id,)))
    if status:
        stmt = stmt.where(Grn.status == status)
    if vendor_id:
        stmt = stmt.where(Grn.vendor_id == vendor_id)
    if boq_id:
        stmt = stmt.where(Grn.boq_id == boq_id)
    if date_from:
        stmt = stmt.where(Grn.received_date >= date_from)
    if date_to:
        stmt = stmt.where(Grn.received_date <= date_to)
    if q:
        stmt = stmt.where(or_(Grn.grn_no.ilike(f"%{q}%"), Grn.invoice_no.ilike(f"%{q}%"), Grn.challan_no.ilike(f"%{q}%")))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Grn.created_at.desc()).offset(paging.offset).limit(paging.limit))).unique().scalars().all()
    names = await user_names(db, [x for g in rows for x in (g.created_by_id, g.posted_by_id)])
    return respond_page(ctx, [_summary(g, names) for g in rows], total, paging)


@router.post("", response_model=Envelope[GrnOut], status_code=201)
async def create_grn(body: GrnIn, db: DB, ctx: Perm("grn.create"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    store, vendor, boq = await _resolve_header(db, ctx, body)
    received = body.received_date or today()
    if received > today():
        raise AppError("VALIDATION_ERROR", "Received date cannot be in the future", 422)
    g = Grn(
        id=uuid.uuid4(),
        grn_no=await next_doc_no(db, DocType.GRN, received),
        store_id=store.id,
        vendor_id=vendor.id,
        boq_id=boq.id,
        received_date=received,
        invoice_no=body.invoice_no,
        invoice_date=body.invoice_date,
        challan_no=body.challan_no,
        vehicle_no=body.vehicle_no,
        remarks=body.remarks,
        status=GrnStatus.DRAFT,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    g.items = await _build_items(db, body, boq)
    _totals(g)
    db.add(g)
    await db.flush()
    await audit(db, ctx, "CREATE", "grn", g.id, f"{g.grn_no} from {vendor.name}", None, snapshot(g))
    await notify(
        db,
        "grn.post",
        type="GRN_DRAFTED",
        title=f"GRN {g.grn_no} ready to post",
        body=f"{vendor.name} · {len(g.items)} item(s)",
        entity_type="grn",
        entity_id=g.id,
        exclude=ctx.user_id,
        store_id=store.id,
    )
    return await finish(db, ctx, lambda: _full(db, g.id), idem)


@router.get("/{grn_id}", response_model=Envelope[GrnOut])
async def get_grn(grn_id: uuid.UUID, db: DB, ctx: Perm("grn.read")):
    await _load_for(db, ctx, grn_id, "grn.read")
    return respond(ctx, await _full(db, grn_id))


@router.put("/{grn_id}", response_model=Envelope[GrnOut])
async def update_grn(grn_id: uuid.UUID, body: GrnUpdateIn, db: DB, ctx: Perm("grn.create")):
    g = await _load_for(db, ctx, grn_id, "grn.create", lock=True)
    ensure_status(g, GrnStatus.DRAFT, action="edit")
    check_version(g, body.version)
    before = snapshot(g)
    store, vendor, boq = await _resolve_header(db, ctx, body)
    received = body.received_date or g.received_date
    for k, v in dict(
        store_id=store.id,
        vendor_id=vendor.id,
        boq_id=boq.id,
        received_date=received,
        invoice_no=body.invoice_no,
        invoice_date=body.invoice_date,
        challan_no=body.challan_no,
        vehicle_no=body.vehicle_no,
        remarks=body.remarks,
    ).items():
        setattr(g, k, v)
    g.items.clear()
    await db.flush()
    g.items.extend(await _build_items(db, body, boq))
    _totals(g)
    bump(g, ctx)
    await db.flush()
    await audit(db, ctx, "UPDATE", "grn", g.id, g.grn_no, before, snapshot(g))
    return await finish(db, ctx, lambda: _full(db, g.id))


@router.post("/{grn_id}/post", response_model=Envelope[GrnOut], summary="Post GRN → central stock +")
async def post_grn(grn_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("grn.post")):
    g = await _load_for(db, ctx, grn_id, "grn.post", lock=True)
    ensure_status(g, GrnStatus.DRAFT, action="post")
    check_version(g, body.version)
    if settings.MAKER_CHECKER_ON_POSTING:
        maker_checker(g.created_by_id, ctx, "post")
    boq = await get_or_404(db, Boq, g.boq_id, "BOQ", lock=True, options=BOQ_FULL)
    if boq.status != BoqStatus.APPROVED:
        raise AppError("BOQ_NOT_APPROVED", f"BOQ {boq.boq_no} is {boq.status}; edit the GRN to point at the approved BOQ", 409)

    # Tolerance: cumulative accepted (all posted GRNs in this BOQ's revision chain) may not exceed planned + tolerance.
    planned = {it.material_id: Decimal(it.planned_qty) for it in boq.items}
    already = await inward_by_material(db, boq)
    tol = Decimal(str(settings.GRN_TOLERANCE_PCT)) / 100
    over = []
    for i in g.items:
        cap = planned.get(i.material_id, Decimal(0)) * (1 + tol)
        total_after = already.get(i.material_id, (Decimal(0), Decimal(0)))[0] + Decimal(i.accepted_qty)
        if total_after > cap:
            over.append(
                {
                    "material": i.material.name,
                    "planned": float(planned.get(i.material_id, 0)),
                    "after_posting": float(total_after),
                    "max_allowed": float(cap),
                }
            )
    if over:
        raise AppError(
            "GRN_EXCEEDS_BOQ",
            f"Receipt would exceed BOQ quantity by more than {settings.GRN_TOLERANCE_PCT:g}%. Revise the BOQ first.",
            409,
            over,
        )

    await post_movements(
        db,
        [
            Movement(
                store_id=g.store_id,
                material_id=i.material_id,
                qty=Decimal(i.accepted_qty),
                inward=True,
                rate=Decimal(i.rate),
                txn_type=TxnType.INWARD,
                ref_type="grn",
                ref_id=g.id,
                ref_no=g.grn_no,
                txn_date=g.received_date,
            )
            for i in g.items
            if i.accepted_qty > 0
        ],
        ctx.user_id,
    )
    g.status, g.posted_by_id, g.posted_at = GrnStatus.POSTED, ctx.user_id, now_utc()
    bump(g, ctx)
    await audit(db, ctx, "POST", "grn", g.id, f"{g.grn_no} posted")
    return await finish(db, ctx, lambda: _full(db, g.id))


@router.post("/{grn_id}/cancel", response_model=Envelope[GrnOut], summary="Cancel draft, or reverse a posted GRN")
async def cancel_grn(grn_id: uuid.UUID, body: CancelIn, db: DB, ctx: Perm("grn.create", "grn.post")):
    g = await get_or_404(db, Grn, grn_id, "GRN", lock=True)
    ensure_status(g, GrnStatus.DRAFT, GrnStatus.POSTED, action="cancel")
    check_version(g, body.version)
    if g.status == GrnStatus.POSTED:
        ensure_covers(ctx, "grn.post", "GRN", store_ids=(g.store_id,))
        # Reverse at the original GRN rate. Fails with INSUFFICIENT_STOCK if the material was already issued.
        await post_movements(
            db,
            [
                Movement(
                    store_id=g.store_id,
                    material_id=i.material_id,
                    qty=Decimal(i.accepted_qty),
                    inward=False,
                    rate=Decimal(i.rate),
                    txn_type=TxnType.REVERSAL,
                    ref_type="grn",
                    ref_id=g.id,
                    ref_no=g.grn_no,
                    txn_date=today(),
                )
                for i in g.items
                if i.accepted_qty > 0
            ],
            ctx.user_id,
        )
    else:
        ensure_covers(ctx, "grn.create", "GRN", store_ids=(g.store_id,))
    g.status, g.cancelled_by_id, g.cancelled_at, g.cancel_reason = GrnStatus.CANCELLED, ctx.user_id, now_utc(), body.reason
    bump(g, ctx)
    await audit(db, ctx, "CANCEL", "grn", g.id, body.reason)
    return await finish(db, ctx, lambda: _full(db, g.id))
