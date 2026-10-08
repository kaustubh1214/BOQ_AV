"""Stock balances, ledger (bin card), alerts, and stock adjustments / physical counts."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select

from app.api.deps import DB, IdemDep, PagingDep, Perm, finish, get_or_404, respond, respond_page
from app.core.errors import AppError
from app.core.responses import CursorEnvelope, Envelope, PageEnvelope
from app.models import Material, StockAdjustment, StockAdjustmentItem, StockBalance, StockLedger, Store
from app.models.enums import AdjustmentReason, AdjustmentStatus, ApprovalAction, DocType, StoreType, TxnType
from app.schemas.common import ApprovalOut, CommentIn, In, NonNegQty, Num, OptNum, Out, PosQty, Rate, VersionIn
from app.schemas.docs import MaterialBrief, StoreBrief, mat_brief
from app.services.common import (
    approvals_for,
    audit,
    bump,
    check_version,
    ensure_status,
    maker_checker,
    next_doc_no,
    now_utc,
    q2,
    q3,
    record_approval,
    snapshot,
    today,
)
from app.services.context import ensure_covers, scope_filter
from app.services.lookups import ensure_unique_materials, materials_by_id
from app.services.names import user_names
from app.services.notify import notify
from app.services.stock_engine import Movement, post_movements

router = APIRouter(tags=["stock"])

ZERO = Decimal("0")


class BalanceOut(Out):
    store: StoreBrief
    store_type: str
    material: MaterialBrief
    category: str
    qty: Num
    avg_rate: OptNum
    value: OptNum
    reorder_level: Num
    below_reorder: bool
    last_movement_at: datetime | None


class LedgerOut(Out):
    id: int
    store_id: uuid.UUID
    material: MaterialBrief
    txn_date: date
    txn_type: str
    ref_type: str
    ref_id: uuid.UUID
    ref_no: str | None
    qty_in: Num
    qty_out: Num
    rate: OptNum
    value: OptNum
    balance_qty: Num
    balance_value: OptNum
    avg_rate_after: OptNum
    created_by_name: str | None = None
    created_at: datetime


@router.get("/stock/balances", response_model=PageEnvelope[BalanceOut])
async def balances(
    db: DB,
    ctx: Perm("stock.read"),
    paging: PagingDep,
    store_id: uuid.UUID | None = None,
    store_type: StoreType | None = None,
    material_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    q: str | None = None,
    below_reorder: bool = False,
    include_zero: bool = False,
):
    stmt = (
        select(StockBalance, Store, Material)
        .join(Store, Store.id == StockBalance.store_id)
        .join(Material, Material.id == StockBalance.material_id)
        .where(scope_filter(ctx.scope("stock.read"), store_cols=(StockBalance.store_id,), site_col=Store.site_id))
    )
    if store_id:
        stmt = stmt.where(StockBalance.store_id == store_id)
    if store_type:
        stmt = stmt.where(Store.type == store_type)
    if material_id:
        stmt = stmt.where(StockBalance.material_id == material_id)
    if category_id:
        stmt = stmt.where(Material.category_id == category_id)
    if q:
        stmt = stmt.where(or_(Material.name.ilike(f"%{q}%"), Material.code.ilike(f"%{q}%")))
    if below_reorder:
        stmt = stmt.where(Material.reorder_level > 0, StockBalance.qty < Material.reorder_level, Store.type == StoreType.CENTRAL)
    if not include_zero:
        stmt = stmt.where(StockBalance.qty > 0)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Store.name, Material.name).offset(paging.offset).limit(paging.limit))).unique().all()
    data = [
        BalanceOut(
            store=StoreBrief(id=s.id, code=s.code, name=s.name),
            store_type=s.type,
            material=mat_brief(m),
            category=m.category.name,
            qty=b.qty,
            avg_rate=b.avg_rate,
            value=q2(b.value),
            reorder_level=m.reorder_level,
            below_reorder=s.type == StoreType.CENTRAL and m.reorder_level > 0 and b.qty < m.reorder_level,
            last_movement_at=b.last_movement_at,
        )
        for b, s, m in rows
    ]
    return respond_page(ctx, data, total, paging)


@router.get("/stock/ledger", response_model=CursorEnvelope[LedgerOut], summary="Movement log for a store (newest first, keyset paging)")
async def ledger(
    db: DB,
    ctx: Perm("stock.read"),
    store_id: uuid.UUID,
    material_id: uuid.UUID | None = None,
    txn_type: TxnType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    store = await get_or_404(db, Store, store_id, "Store")
    ensure_covers(ctx, "stock.read", "Store", site_id=store.site_id, store_ids=(store.id,))
    stmt = select(StockLedger).where(StockLedger.store_id == store_id)
    if material_id:
        stmt = stmt.where(StockLedger.material_id == material_id)
    if txn_type:
        stmt = stmt.where(StockLedger.txn_type == txn_type)
    if date_from:
        stmt = stmt.where(StockLedger.txn_date >= date_from)
    if date_to:
        stmt = stmt.where(StockLedger.txn_date <= date_to)
    if cursor:
        stmt = stmt.where(StockLedger.id < cursor)
    rows = (await db.execute(stmt.order_by(StockLedger.id.desc()).limit(limit))).scalars().all()
    mats = (
        {m.id: m for m in (await db.execute(select(Material).where(Material.id.in_({r.material_id for r in rows})))).unique().scalars()}
        if rows
        else {}
    )
    names = await user_names(db, [r.created_by_id for r in rows])
    data = [
        LedgerOut(
            id=r.id,
            store_id=r.store_id,
            material=mat_brief(mats[r.material_id]),
            txn_date=r.txn_date,
            txn_type=r.txn_type,
            ref_type=r.ref_type,
            ref_id=r.ref_id,
            ref_no=r.ref_no,
            qty_in=r.qty_in,
            qty_out=r.qty_out,
            rate=r.rate,
            value=q2(r.value),
            balance_qty=r.balance_qty,
            balance_value=q2(r.balance_value),
            avg_rate_after=r.avg_rate_after,
            created_by_name=names.get(r.created_by_id),
            created_at=r.created_at,
        )
        for r in rows
    ]
    from app.services.common import scrub

    return {
        "success": True,
        "data": scrub(data, ctx.show_finance),
        "meta": {"limit": limit, "nextCursor": data[-1].id if len(data) == limit else None},
    }


# ------------------------------------------------------------------ adjustments


class AdjItemIn(In):
    material_id: uuid.UUID
    qty: PosQty | None = Field(None, description="OPENING: qty added · DAMAGE/WASTAGE/THEFT: qty written off")
    physical_qty: NonNegQty | None = Field(None, description="PHYSICAL_COUNT: counted quantity")
    rate: Rate | None = Field(None, description="OPENING: valuation rate per base UOM")
    remarks: Annotated[str | None, Field(max_length=200)] = None


class AdjIn(In):
    store_id: uuid.UUID
    reason: AdjustmentReason
    adj_date: date | None = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[AdjItemIn], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _per_reason(self):
        for i in self.items:
            if self.reason == AdjustmentReason.PHYSICAL_COUNT:
                if i.physical_qty is None:
                    raise ValueError("physical_qty is required for a physical count")
            elif i.qty is None:
                raise ValueError("qty is required")
            if self.reason == AdjustmentReason.OPENING and i.rate is None:
                raise ValueError("rate is required for opening stock")
        return self


class AdjUpdateIn(AdjIn):
    version: int


class AdjItemOut(Out):
    id: uuid.UUID
    line_no: int
    material: MaterialBrief
    physical_qty: OptNum
    system_qty: OptNum
    qty_delta: OptNum
    rate: OptNum
    remarks: str | None


class AdjOut(Out):
    id: uuid.UUID
    adj_no: str
    status: str
    version: int
    store: StoreBrief
    adj_date: date
    reason: str
    remarks: str | None
    created_by_id: uuid.UUID | None
    created_by_name: str | None = None
    approved_by_name: str | None = None
    submitted_at: datetime | None
    approved_at: datetime | None
    created_at: datetime
    items: list[AdjItemOut]
    approvals: list[ApprovalOut]


_LOSS = (AdjustmentReason.DAMAGE, AdjustmentReason.WASTAGE, AdjustmentReason.THEFT)


async def _adj_full(db, adj_id: uuid.UUID) -> AdjOut:
    a = await get_or_404(db, StockAdjustment, adj_id, "Adjustment")
    approvals = await approvals_for(db, "adjustment", a.id)
    names = await user_names(db, [a.created_by_id, a.approved_by_id, *[x.actor_id for x in approvals]])
    return AdjOut(
        id=a.id,
        adj_no=a.adj_no,
        status=a.status,
        version=a.version,
        store=StoreBrief(id=a.store.id, code=a.store.code, name=a.store.name),
        adj_date=a.adj_date,
        reason=a.reason,
        remarks=a.remarks,
        created_by_id=a.created_by_id,
        created_by_name=names.get(a.created_by_id),
        approved_by_name=names.get(a.approved_by_id),
        submitted_at=a.submitted_at,
        approved_at=a.approved_at,
        created_at=a.created_at,
        items=[
            AdjItemOut(
                id=i.id,
                line_no=i.line_no,
                material=mat_brief(i.material),
                physical_qty=i.physical_qty,
                system_qty=i.system_qty,
                qty_delta=i.qty_delta,
                rate=i.rate,
                remarks=i.remarks,
            )
            for i in a.items
        ],
        approvals=[
            ApprovalOut(
                stage=x.stage,
                action=x.action,
                actor_id=x.actor_id,
                actor_name=names.get(x.actor_id),
                comment=x.comment,
                acted_at=x.acted_at,
            )
            for x in approvals
        ],
    )


async def _adj_items(db, body: AdjIn) -> list[StockAdjustmentItem]:
    ensure_unique_materials(body.items)
    await materials_by_id(db, [i.material_id for i in body.items])
    out = []
    for n, i in enumerate(body.items, start=1):
        delta = None
        if body.reason == AdjustmentReason.OPENING:
            delta = q3(i.qty)
        elif body.reason in _LOSS:
            delta = -q3(i.qty)
        out.append(
            StockAdjustmentItem(
                id=uuid.uuid4(),
                line_no=n,
                material_id=i.material_id,
                physical_qty=i.physical_qty,
                qty_delta=delta,
                rate=i.rate,
                remarks=i.remarks,
            )
        )
    return out


async def _adj_store(db, ctx, store_id: uuid.UUID, perm: str) -> Store:
    store = await db.get(Store, store_id)
    if store is None or not store.is_active:
        raise AppError("INVALID_REFERENCE", "Store not found", 422)
    ensure_covers(ctx, perm, "Store", site_id=store.site_id, store_ids=(store.id,))
    return store


@router.get("/stock-adjustments", response_model=PageEnvelope[AdjOut])
async def list_adjustments(
    db: DB,
    ctx: Perm("stock.read"),
    paging: PagingDep,
    status: AdjustmentStatus | None = None,
    store_id: uuid.UUID | None = None,
    reason: AdjustmentReason | None = None,
):
    A = StockAdjustment
    stmt = (
        select(A)
        .join(Store, Store.id == A.store_id)
        .where(scope_filter(ctx.scope("stock.read"), store_cols=(A.store_id,), site_col=Store.site_id))
    )
    for col, val in ((A.status, status), (A.store_id, store_id), (A.reason, reason)):
        if val is not None:
            stmt = stmt.where(col == val)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    ids = (
        (await db.execute(stmt.with_only_columns(A.id).order_by(A.created_at.desc()).offset(paging.offset).limit(paging.limit)))
        .scalars()
        .all()
    )
    return respond_page(ctx, [await _adj_full(db, i) for i in ids], total, paging)


@router.post("/stock-adjustments", response_model=Envelope[AdjOut], status_code=201)
async def create_adjustment(body: AdjIn, db: DB, ctx: Perm("adjustment.create"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    store = await _adj_store(db, ctx, body.store_id, "adjustment.create")
    adj_date = body.adj_date or today()
    a = StockAdjustment(
        id=uuid.uuid4(),
        adj_no=await next_doc_no(db, DocType.ADJUSTMENT, adj_date),
        store_id=store.id,
        adj_date=adj_date,
        reason=body.reason,
        remarks=body.remarks,
        status=AdjustmentStatus.DRAFT,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    a.items = await _adj_items(db, body)
    db.add(a)
    await db.flush()
    await audit(db, ctx, "CREATE", "adjustment", a.id, f"{a.adj_no} {a.reason}", None, snapshot(a))
    return await finish(db, ctx, lambda: _adj_full(db, a.id), idem)


async def _adj_for(db, ctx, adj_id, perm) -> StockAdjustment:
    a = await get_or_404(db, StockAdjustment, adj_id, "Adjustment", lock=True)
    ensure_covers(ctx, perm, "Adjustment", site_id=a.store.site_id, store_ids=(a.store_id,))
    return a


@router.get("/stock-adjustments/{adj_id}", response_model=Envelope[AdjOut])
async def get_adjustment(adj_id: uuid.UUID, db: DB, ctx: Perm("stock.read")):
    a = await get_or_404(db, StockAdjustment, adj_id, "Adjustment")
    ensure_covers(ctx, "stock.read", "Adjustment", site_id=a.store.site_id, store_ids=(a.store_id,))
    return respond(ctx, await _adj_full(db, adj_id))


@router.put("/stock-adjustments/{adj_id}", response_model=Envelope[AdjOut])
async def update_adjustment(adj_id: uuid.UUID, body: AdjUpdateIn, db: DB, ctx: Perm("adjustment.create")):
    a = await _adj_for(db, ctx, adj_id, "adjustment.create")
    ensure_status(a, AdjustmentStatus.DRAFT, AdjustmentStatus.REJECTED, action="edit")
    check_version(a, body.version)
    if body.store_id != a.store_id:
        raise AppError("VALIDATION_ERROR", "Store cannot be changed; cancel and create a new adjustment", 422)
    a.reason, a.remarks, a.adj_date = body.reason, body.remarks, body.adj_date or a.adj_date
    a.items.clear()
    await db.flush()
    a.items.extend(await _adj_items(db, body))
    a.status = AdjustmentStatus.DRAFT
    bump(a, ctx)
    await audit(db, ctx, "UPDATE", "adjustment", a.id, a.adj_no)
    return await finish(db, ctx, lambda: _adj_full(db, a.id))


@router.post("/stock-adjustments/{adj_id}/submit", response_model=Envelope[AdjOut])
async def submit_adjustment(adj_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("adjustment.create")):
    a = await _adj_for(db, ctx, adj_id, "adjustment.create")
    ensure_status(a, AdjustmentStatus.DRAFT, AdjustmentStatus.REJECTED, action="submit")
    check_version(a, body.version)
    a.status, a.submitted_at = AdjustmentStatus.SUBMITTED, now_utc()
    bump(a, ctx)
    await record_approval(db, ctx, "adjustment", a.id, "STORE", ApprovalAction.SUBMITTED, None)
    await audit(db, ctx, "SUBMIT", "adjustment", a.id, a.adj_no)
    await notify(
        db,
        "adjustment.approve",
        type="ADJUSTMENT_SUBMITTED",
        title=f"Stock adjustment {a.adj_no} awaiting approval",
        body=a.reason,
        entity_type="adjustment",
        entity_id=a.id,
        exclude=ctx.user_id,
        store_id=a.store_id,
        site_id=a.store.site_id,
    )
    return await finish(db, ctx, lambda: _adj_full(db, a.id))


@router.post("/stock-adjustments/{adj_id}/approve", response_model=Envelope[AdjOut], summary="Approve → stock ±")
async def approve_adjustment(adj_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("adjustment.approve")):
    a = await _adj_for(db, ctx, adj_id, "adjustment.approve")
    ensure_status(a, AdjustmentStatus.SUBMITTED, action="approve")
    check_version(a, body.version)
    maker_checker(a.created_by_id, ctx)

    if a.reason == AdjustmentReason.PHYSICAL_COUNT:
        # Lock balances first so the system qty we compare against can't move under us.
        from app.services.stock_engine import _lock_balances

        locked = await _lock_balances(db, [(a.store_id, i.material_id) for i in a.items])
        for i in a.items:
            sys_qty = Decimal(locked[(a.store_id, i.material_id)].qty)
            i.system_qty = sys_qty
            i.qty_delta = q3(Decimal(i.physical_qty) - sys_qty)

    movements = []
    for i in a.items:
        delta = Decimal(i.qty_delta or 0)
        if delta == 0:
            continue
        movements.append(
            Movement(
                store_id=a.store_id,
                material_id=i.material_id,
                qty=abs(delta),
                inward=delta > 0,
                rate=Decimal(i.rate) if i.rate is not None else None,
                txn_type=TxnType.ADJUSTMENT,
                ref_type="adjustment",
                ref_id=a.id,
                ref_no=a.adj_no,
                txn_date=a.adj_date,
            )
        )
    # Count surplus with no rate given: value at the current average (0 if the item never had stock).
    for m in movements:
        if m.inward and m.rate is None:
            bal = await db.get(StockBalance, (m.store_id, m.material_id))
            m.rate = Decimal(bal.avg_rate) if bal else ZERO
    posted = await post_movements(db, movements, ctx.user_id)
    rates = {p.movement.material_id: p.rate for p in posted}
    for i in a.items:
        if i.rate is None and i.material_id in rates:
            i.rate = rates[i.material_id]
    a.status, a.approved_by_id, a.approved_at = AdjustmentStatus.APPROVED, ctx.user_id, now_utc()
    bump(a, ctx)
    await record_approval(db, ctx, "adjustment", a.id, "STORE_MANAGER", ApprovalAction.APPROVED, body.comment)
    await audit(db, ctx, "APPROVE", "adjustment", a.id, a.adj_no)
    return await finish(db, ctx, lambda: _adj_full(db, a.id))


@router.post("/stock-adjustments/{adj_id}/reject", response_model=Envelope[AdjOut])
async def reject_adjustment(adj_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("adjustment.approve")):
    if not body.comment:
        raise AppError("VALIDATION_ERROR", "A comment is required when rejecting", 422)
    a = await _adj_for(db, ctx, adj_id, "adjustment.approve")
    ensure_status(a, AdjustmentStatus.SUBMITTED, action="reject")
    check_version(a, body.version)
    a.status = AdjustmentStatus.REJECTED
    bump(a, ctx)
    await record_approval(db, ctx, "adjustment", a.id, "STORE_MANAGER", ApprovalAction.REJECTED, body.comment)
    await audit(db, ctx, "REJECT", "adjustment", a.id, body.comment)
    return await finish(db, ctx, lambda: _adj_full(db, a.id))


@router.post("/stock-adjustments/{adj_id}/cancel", response_model=Envelope[AdjOut])
async def cancel_adjustment(adj_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("adjustment.create")):
    a = await _adj_for(db, ctx, adj_id, "adjustment.create")
    ensure_status(a, AdjustmentStatus.DRAFT, AdjustmentStatus.REJECTED, action="cancel")
    check_version(a, body.version)
    a.status = AdjustmentStatus.CANCELLED
    bump(a, ctx)
    await audit(db, ctx, "CANCEL", "adjustment", a.id, a.adj_no)
    return await finish(db, ctx, lambda: _adj_full(db, a.id))
