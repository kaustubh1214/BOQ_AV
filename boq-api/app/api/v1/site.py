"""Site operations: site stock (with avg daily consumption / days of cover), daily consumption, returns."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DB, IdemDep, PagingDep, Perm, finish, get_or_404, respond, respond_page
from app.core.errors import AppError
from app.core.responses import Envelope, PageEnvelope
from app.models import ConsumptionEntry, Material, ReturnNote, ReturnNoteItem, StockBalance
from app.models.enums import DocType, EntryStatus, ReturnStatus, TxnType
from app.schemas.common import CancelIn, In, NonNegQty, Num, OptNum, Out, PosQty, VersionIn
from app.schemas.docs import MaterialBrief, SiteBrief, mat_brief
from app.services.common import audit, bump, check_version, ensure_status, next_doc_no, now_utc, q2, q3, snapshot, to_base_uom, today
from app.services.context import ensure_covers, scope_filter
from app.services.lookups import central_store, ensure_unique_materials, get_site, materials_by_id, site_store
from app.services.names import user_names
from app.services.notify import notify
from app.services.stock_engine import Movement, post_movements

router = APIRouter(tags=["site operations"])

ZERO = Decimal("0")
BACKDATE_DAYS = 30


# ------------------------------------------------------------------ site stock


class SiteStockLine(Out):
    material: MaterialBrief
    qty: Num
    avg_rate: OptNum
    value: OptNum
    consumed_7d: Num
    consumed_30d: Num
    avg_daily_7d: Num
    avg_daily_30d: Num
    days_of_cover: OptNum
    in_transit: Num


async def site_stock_lines(db, site_id: uuid.UUID) -> list[SiteStockLine]:
    from app.models import IssueNote, IssueNoteItem
    from app.models.enums import IssueStatus

    store = await site_store(db, site_id)
    t = today()
    bal = {b.material_id: b for b in (await db.execute(select(StockBalance).where(StockBalance.store_id == store.id))).scalars()}
    cons = await db.execute(
        select(
            ConsumptionEntry.material_id,
            func.sum(ConsumptionEntry.qty).filter(ConsumptionEntry.entry_date > t - timedelta(days=7)),
            func.sum(ConsumptionEntry.qty),
        )
        .where(
            ConsumptionEntry.site_id == site_id,
            ConsumptionEntry.status == EntryStatus.POSTED,
            ConsumptionEntry.entry_date > t - timedelta(days=30),
        )
        .group_by(ConsumptionEntry.material_id)
    )
    consumed = {m: (Decimal(a or 0), Decimal(b or 0)) for m, a, b in cons}
    transit = dict(
        (
            await db.execute(
                select(IssueNoteItem.material_id, func.sum(IssueNoteItem.issued_qty))
                .join(IssueNote, IssueNote.id == IssueNoteItem.issue_note_id)
                .where(IssueNote.site_id == site_id, IssueNote.status == IssueStatus.ISSUED)
                .group_by(IssueNoteItem.material_id)
            )
        ).all()
    )
    mids = set(bal) | set(consumed) | set(transit)
    mats = {m.id: m for m in (await db.execute(select(Material).where(Material.id.in_(mids)))).unique().scalars()} if mids else {}
    out = []
    for mid in sorted(mids, key=lambda x: mats[x].name):
        b = bal.get(mid)
        qty = Decimal(b.qty) if b else ZERO
        c7, c30 = consumed.get(mid, (ZERO, ZERO))
        avg7, avg30 = q3(c7 / 7), q3(c30 / 30)
        basis = avg7 if avg7 > 0 else avg30
        out.append(
            SiteStockLine(
                material=mat_brief(mats[mid]),
                qty=qty,
                avg_rate=b.avg_rate if b else None,
                value=q2(b.value) if b else None,
                consumed_7d=c7,
                consumed_30d=c30,
                avg_daily_7d=avg7,
                avg_daily_30d=avg30,
                days_of_cover=q2(qty / basis) if basis > 0 else None,
                in_transit=Decimal(transit.get(mid) or 0),
            )
        )
    return out


@router.get("/site/stock", response_model=Envelope[list[SiteStockLine]], summary="Site stock with avg daily consumption and days of cover")
async def site_stock(db: DB, ctx: Perm("site.read", "stock.read"), site_id: uuid.UUID):
    site = await get_site(db, site_id)
    if not (
        ctx.covers("site.read", site_id=site.id, project_id=site.project_id)
        or ctx.covers("stock.read", site_id=site.id, project_id=site.project_id)
    ):
        raise AppError("NOT_FOUND", "Site not found", 404)
    return respond(ctx, await site_stock_lines(db, site.id))


# ------------------------------------------------------------------ consumption


class ConsumptionLineIn(In):
    material_id: uuid.UUID
    qty: PosQty
    uom_id: uuid.UUID | None = None
    activity: Annotated[str | None, Field(max_length=200)] = None
    remarks: Annotated[str | None, Field(max_length=500)] = None


class ConsumptionIn(In):
    site_id: uuid.UUID
    entry_date: date | None = None
    activity: Annotated[str | None, Field(max_length=200, description="Default activity for all lines")] = None
    items: Annotated[list[ConsumptionLineIn], Field(min_length=1, max_length=100)]


class ConsumptionOut(Out):
    id: uuid.UUID
    site: SiteBrief
    entry_date: date
    material: MaterialBrief
    qty: Num
    rate: OptNum
    amount: OptNum
    activity: str | None
    remarks: str | None
    status: str
    created_by_name: str | None = None
    created_at: datetime


def _cons_out(e: ConsumptionEntry, names: dict) -> ConsumptionOut:
    return ConsumptionOut(
        id=e.id,
        site=SiteBrief(id=e.site.id, code=e.site.code, name=e.site.name, project_id=e.site.project_id, project_name=e.site.project.name),
        entry_date=e.entry_date,
        material=mat_brief(e.material),
        qty=e.qty,
        rate=e.rate,
        amount=e.amount,
        activity=e.activity,
        remarks=e.remarks,
        status=e.status,
        created_by_name=names.get(e.created_by_id),
        created_at=e.created_at,
    )


@router.get("/consumptions", response_model=PageEnvelope[ConsumptionOut])
async def list_consumptions(
    db: DB,
    ctx: Perm("site.read"),
    paging: PagingDep,
    site_id: uuid.UUID | None = None,
    material_id: uuid.UUID | None = None,
    status: EntryStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    from app.models import Site

    E = ConsumptionEntry
    stmt = (
        select(E)
        .join(Site, Site.id == E.site_id)
        .where(scope_filter(ctx.scope("site.read"), store_cols=(E.store_id,), site_col=E.site_id, project_col=Site.project_id))
    )
    for col, val in ((E.site_id, site_id), (E.material_id, material_id), (E.status, status)):
        if val is not None:
            stmt = stmt.where(col == val)
    if date_from:
        stmt = stmt.where(E.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(E.entry_date <= date_to)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(E.entry_date.desc(), E.created_at.desc()).offset(paging.offset).limit(paging.limit)))
        .unique()
        .scalars()
        .all()
    )
    names = await user_names(db, [e.created_by_id for e in rows])
    return respond_page(ctx, [_cons_out(e, names) for e in rows], total, paging)


@router.post(
    "/consumptions", response_model=Envelope[list[ConsumptionOut]], status_code=201, summary="Log daily consumption (site stock −)"
)
async def create_consumption(body: ConsumptionIn, db: DB, ctx: Perm("site.consume"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    site = await get_site(db, body.site_id)
    ensure_covers(ctx, "site.consume", "Site", site_id=site.id, project_id=site.project_id)
    entry_date = body.entry_date or today()
    if entry_date > today() or entry_date < today() - timedelta(days=BACKDATE_DAYS):
        raise AppError("VALIDATION_ERROR", f"Entry date must be within the last {BACKDATE_DAYS} days", 422)
    store = await site_store(db, site.id)
    mats = await materials_by_id(db, [i.material_id for i in body.items])
    ensure_unique_materials(body.items)
    entries = []
    for i in body.items:
        qty, _ = await to_base_uom(db, mats[i.material_id], i.qty, None, i.uom_id)
        e = ConsumptionEntry(
            id=uuid.uuid4(),
            site_id=site.id,
            store_id=store.id,
            entry_date=entry_date,
            material_id=i.material_id,
            qty=qty,
            activity=i.activity or body.activity,
            remarks=i.remarks,
            status=EntryStatus.POSTED,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        db.add(e)
        entries.append(e)
    await db.flush()
    posted = await post_movements(
        db,
        [
            Movement(
                store_id=store.id,
                material_id=e.material_id,
                qty=Decimal(e.qty),
                inward=False,
                txn_type=TxnType.CONSUMPTION,
                ref_type="consumption",
                ref_id=e.id,
                ref_no=None,
                txn_date=entry_date,
            )
            for e in entries
        ],
        ctx.user_id,
    )
    for e, p in zip(entries, posted, strict=True):
        e.rate, e.amount = p.rate, q2(p.value)
    await audit(db, ctx, "CREATE", "consumption", site.id, f"{len(entries)} consumption line(s) on {entry_date}")
    ids = [e.id for e in entries]

    async def load():
        rows = (await db.execute(select(ConsumptionEntry).where(ConsumptionEntry.id.in_(ids)))).unique().scalars().all()
        names = await user_names(db, [ctx.user_id])
        return [_cons_out(e, names) for e in rows]

    return await finish(db, ctx, load, idem)


@router.post("/consumptions/{entry_id}/cancel", response_model=Envelope[ConsumptionOut], summary="Reverse a consumption entry")
async def cancel_consumption(entry_id: uuid.UUID, body: CancelIn, db: DB, ctx: Perm("site.consume")):
    e = await get_or_404(db, ConsumptionEntry, entry_id, "Consumption entry", lock=True)
    ensure_covers(ctx, "site.consume", "Consumption entry", site_id=e.site_id, store_ids=(e.store_id,))
    ensure_status(e, EntryStatus.POSTED, action="cancel")
    if e.entry_date < today() - timedelta(days=BACKDATE_DAYS):
        raise AppError("TOO_OLD", f"Entries older than {BACKDATE_DAYS} days can only be corrected with a stock adjustment", 409)
    await post_movements(
        db,
        [
            Movement(
                store_id=e.store_id,
                material_id=e.material_id,
                qty=Decimal(e.qty),
                inward=True,
                rate=Decimal(e.rate or 0),
                txn_type=TxnType.REVERSAL,
                ref_type="consumption",
                ref_id=e.id,
                ref_no=None,
                txn_date=today(),
            )
        ],
        ctx.user_id,
    )
    e.status, e.cancelled_by_id, e.cancelled_at = EntryStatus.CANCELLED, ctx.user_id, now_utc()
    e.remarks = f"{e.remarks or ''} [Cancelled: {body.reason}]".strip()
    await audit(db, ctx, "CANCEL", "consumption", e.id, body.reason)

    async def load():
        x = await get_or_404(db, ConsumptionEntry, entry_id)
        return _cons_out(x, await user_names(db, [x.created_by_id]))

    return await finish(db, ctx, load)


# ------------------------------------------------------------------ returns (site → central store)


class ReturnItemIn(In):
    material_id: uuid.UUID
    qty: PosQty
    uom_id: uuid.UUID | None = None


class ReturnIn(In):
    site_id: uuid.UUID
    return_date: date | None = None
    reason: Annotated[str | None, Field(max_length=200)] = None
    vehicle_no: Annotated[str | None, Field(max_length=20)] = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[ReturnItemIn], Field(min_length=1, max_length=200)]


class ReturnUpdateIn(In):
    return_date: date | None = None
    reason: Annotated[str | None, Field(max_length=200)] = None
    vehicle_no: Annotated[str | None, Field(max_length=20)] = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[ReturnItemIn], Field(min_length=1, max_length=200)]
    version: int


class ReturnReceiveLine(In):
    item_id: uuid.UUID
    received_qty: NonNegQty


class ReturnReceiveIn(In):
    items: list[ReturnReceiveLine] = Field([], description="Omit to receive every line in full")
    version: int | None = None


class ReturnItemOut(Out):
    id: uuid.UUID
    line_no: int
    material: MaterialBrief
    qty: Num
    rate: OptNum
    received_qty: OptNum


class ReturnOut(Out):
    id: uuid.UUID
    return_no: str
    status: str
    version: int
    site: SiteBrief
    from_store_id: uuid.UUID
    to_store_id: uuid.UUID
    return_date: date
    reason: str | None
    vehicle_no: str | None
    remarks: str | None
    created_by_name: str | None = None
    dispatched_at: datetime | None
    received_at: datetime | None
    created_at: datetime
    items: list[ReturnItemOut]


async def _ret_full(db, ret_id: uuid.UUID) -> ReturnOut:
    r = await get_or_404(db, ReturnNote, ret_id, "Return")
    names = await user_names(db, [r.created_by_id])
    return ReturnOut(
        id=r.id,
        return_no=r.return_no,
        status=r.status,
        version=r.version,
        site=SiteBrief(id=r.site.id, code=r.site.code, name=r.site.name, project_id=r.site.project_id, project_name=r.site.project.name),
        from_store_id=r.from_store_id,
        to_store_id=r.to_store_id,
        return_date=r.return_date,
        reason=r.reason,
        vehicle_no=r.vehicle_no,
        remarks=r.remarks,
        created_by_name=names.get(r.created_by_id),
        dispatched_at=r.dispatched_at,
        received_at=r.received_at,
        created_at=r.created_at,
        items=[
            ReturnItemOut(id=i.id, line_no=i.line_no, material=mat_brief(i.material), qty=i.qty, rate=i.rate, received_qty=i.received_qty)
            for i in r.items
        ],
    )


async def _ret_items(db, items: list[ReturnItemIn]) -> list[ReturnNoteItem]:
    ensure_unique_materials(items)
    mats = await materials_by_id(db, [i.material_id for i in items])
    out = []
    for n, i in enumerate(items, start=1):
        qty, _ = await to_base_uom(db, mats[i.material_id], i.qty, None, i.uom_id)
        out.append(ReturnNoteItem(id=uuid.uuid4(), line_no=n, material_id=i.material_id, qty=qty))
    return out


@router.get("/returns", response_model=PageEnvelope[ReturnOut])
async def list_returns(
    db: DB,
    ctx: Perm("site.read", "return.receive"),
    paging: PagingDep,
    status: ReturnStatus | None = None,
    site_id: uuid.UUID | None = None,
    queue: Annotated[str | None, Query(pattern="^(receive)$")] = None,
):
    R = ReturnNote
    scope = ctx.scope("site.read")
    scope.merge(ctx.scope("return.receive"))
    stmt = select(R).where(scope_filter(scope, store_cols=(R.from_store_id, R.to_store_id), site_col=R.site_id, project_col=R.project_id))
    if queue == "receive":
        stmt = stmt.where(R.status == ReturnStatus.DISPATCHED, scope_filter(ctx.scope("return.receive"), store_cols=(R.to_store_id,)))
    if status:
        stmt = stmt.where(R.status == status)
    if site_id:
        stmt = stmt.where(R.site_id == site_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    ids = (
        (await db.execute(stmt.with_only_columns(R.id).order_by(R.created_at.desc()).offset(paging.offset).limit(paging.limit)))
        .scalars()
        .all()
    )
    return respond_page(ctx, [await _ret_full(db, i) for i in ids], total, paging)


@router.post("/returns", response_model=Envelope[ReturnOut], status_code=201)
async def create_return(body: ReturnIn, db: DB, ctx: Perm("site.return"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    site = await get_site(db, body.site_id)
    ensure_covers(ctx, "site.return", "Site", site_id=site.id, project_id=site.project_id)
    rdate = body.return_date or today()
    r = ReturnNote(
        id=uuid.uuid4(),
        return_no=await next_doc_no(db, DocType.RETURN, rdate),
        site_id=site.id,
        project_id=site.project_id,
        from_store_id=(await site_store(db, site.id)).id,
        to_store_id=(await central_store(db)).id,
        return_date=rdate,
        reason=body.reason,
        vehicle_no=body.vehicle_no,
        remarks=body.remarks,
        status=ReturnStatus.DRAFT,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    r.items = await _ret_items(db, body.items)
    db.add(r)
    await db.flush()
    await audit(db, ctx, "CREATE", "return", r.id, r.return_no, None, snapshot(r))
    return await finish(db, ctx, lambda: _ret_full(db, r.id), idem)


async def _ret_for(db, ctx, ret_id, perm, lock=True) -> ReturnNote:
    r = await get_or_404(db, ReturnNote, ret_id, "Return", lock=lock)
    ensure_covers(ctx, perm, "Return", site_id=r.site_id, project_id=r.project_id, store_ids=(r.from_store_id, r.to_store_id))
    return r


@router.get("/returns/{ret_id}", response_model=Envelope[ReturnOut])
async def get_return(ret_id: uuid.UUID, db: DB, ctx: Perm("site.read", "return.receive")):
    r = await get_or_404(db, ReturnNote, ret_id, "Return")
    if not (
        ctx.covers("site.read", site_id=r.site_id, project_id=r.project_id) or ctx.covers("return.receive", store_ids=(r.to_store_id,))
    ):
        raise AppError("NOT_FOUND", "Return not found", 404)
    return respond(ctx, await _ret_full(db, ret_id))


@router.put("/returns/{ret_id}", response_model=Envelope[ReturnOut])
async def update_return(ret_id: uuid.UUID, body: ReturnUpdateIn, db: DB, ctx: Perm("site.return")):
    r = await _ret_for(db, ctx, ret_id, "site.return")
    ensure_status(r, ReturnStatus.DRAFT, action="edit")
    check_version(r, body.version)
    for k in ("reason", "vehicle_no", "remarks"):
        setattr(r, k, getattr(body, k))
    r.return_date = body.return_date or r.return_date
    r.items.clear()
    await db.flush()
    r.items.extend(await _ret_items(db, body.items))
    bump(r, ctx)
    await audit(db, ctx, "UPDATE", "return", r.id, r.return_no)
    return await finish(db, ctx, lambda: _ret_full(db, r.id))


@router.post("/returns/{ret_id}/dispatch", response_model=Envelope[ReturnOut], summary="Dispatch to central store (site stock −)")
async def dispatch_return(ret_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("site.return")):
    r = await _ret_for(db, ctx, ret_id, "site.return")
    ensure_status(r, ReturnStatus.DRAFT, action="dispatch")
    check_version(r, body.version)
    posted = await post_movements(
        db,
        [
            Movement(
                store_id=r.from_store_id,
                material_id=i.material_id,
                qty=Decimal(i.qty),
                inward=False,
                txn_type=TxnType.RETURN_OUT,
                ref_type="return",
                ref_id=r.id,
                ref_no=r.return_no,
                txn_date=today(),
            )
            for i in r.items
        ],
        ctx.user_id,
    )
    for i, p in zip(r.items, posted, strict=True):
        i.rate = p.rate
    r.status, r.dispatched_by_id, r.dispatched_at = ReturnStatus.DISPATCHED, ctx.user_id, now_utc()
    bump(r, ctx)
    await audit(db, ctx, "DISPATCH", "return", r.id, r.return_no)
    await notify(
        db,
        "return.receive",
        type="RETURN_DISPATCHED",
        title=f"Return {r.return_no} on the way to the store",
        entity_type="return",
        entity_id=r.id,
        exclude=ctx.user_id,
        store_id=r.to_store_id,
    )
    return await finish(db, ctx, lambda: _ret_full(db, r.id))


@router.post("/returns/{ret_id}/receive", response_model=Envelope[ReturnOut], summary="Receive at central store (central stock +)")
async def receive_return(ret_id: uuid.UUID, body: ReturnReceiveIn, db: DB, ctx: Perm("return.receive")):
    r = await get_or_404(db, ReturnNote, ret_id, "Return", lock=True)
    ensure_covers(ctx, "return.receive", "Return", store_ids=(r.to_store_id,))
    ensure_status(r, ReturnStatus.DISPATCHED, action="receive")
    check_version(r, body.version)
    given = {ln.item_id: ln.received_qty for ln in body.items}
    for i in r.items:
        qty = q3(given.get(i.id, Decimal(i.qty)))
        if qty > Decimal(i.qty):
            raise AppError("VALIDATION_ERROR", f"{i.material.name}: received cannot exceed dispatched quantity", 422)
        i.received_qty = qty
    await post_movements(
        db,
        [
            Movement(
                store_id=r.to_store_id,
                material_id=i.material_id,
                qty=Decimal(i.received_qty),
                inward=True,
                rate=Decimal(i.rate),
                txn_type=TxnType.RETURN_IN,
                ref_type="return",
                ref_id=r.id,
                ref_no=r.return_no,
                txn_date=today(),
            )
            for i in r.items
            if Decimal(i.received_qty) > 0
        ],
        ctx.user_id,
    )
    r.status, r.received_by_id, r.received_at = ReturnStatus.RECEIVED, ctx.user_id, now_utc()
    bump(r, ctx)
    await audit(db, ctx, "RECEIVE", "return", r.id, r.return_no)
    return await finish(db, ctx, lambda: _ret_full(db, r.id))


@router.post("/returns/{ret_id}/cancel", response_model=Envelope[ReturnOut])
async def cancel_return(ret_id: uuid.UUID, body: CancelIn, db: DB, ctx: Perm("site.return")):
    r = await _ret_for(db, ctx, ret_id, "site.return")
    ensure_status(r, ReturnStatus.DRAFT, ReturnStatus.DISPATCHED, action="cancel")
    check_version(r, body.version)
    if r.status == ReturnStatus.DISPATCHED:
        await post_movements(
            db,
            [
                Movement(
                    store_id=r.from_store_id,
                    material_id=i.material_id,
                    qty=Decimal(i.qty),
                    inward=True,
                    rate=Decimal(i.rate),
                    txn_type=TxnType.REVERSAL,
                    ref_type="return",
                    ref_id=r.id,
                    ref_no=r.return_no,
                    txn_date=today(),
                )
                for i in r.items
            ],
            ctx.user_id,
        )
    r.status = ReturnStatus.CANCELLED
    r.remarks = f"{r.remarks or ''} [Cancelled: {body.reason}]".strip()
    bump(r, ctx)
    await audit(db, ctx, "CANCEL", "return", r.id, body.reason)
    return await finish(db, ctx, lambda: _ret_full(db, r.id))
