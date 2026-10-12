"""Issue Note: central store → site, against approved RFQ lines.

DRAFT → ISSUED (central stock −, at weighted average; material is in transit)
      → RECEIVED / PARTIALLY_RECEIVED (site acknowledges; site stock + received; shortage/damage logged)
DRAFT/ISSUED → CANCELLED (an ISSUED note is reversed back into the central store)
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import func, or_, select

from app.api.deps import DB, IdemDep, PagingDep, Perm, finish, get_or_404, respond, respond_page
from app.core.errors import AppError
from app.core.responses import Envelope, PageEnvelope
from app.models import IssueNote, IssueNoteItem, MaterialRequest, MaterialRequestItem, Store
from app.models.enums import DocType, IssueStatus, RfqStatus, TxnType
from app.schemas.common import CancelIn, In, NonNegQty, Num, OptNum, Out, PosQty, VersionIn
from app.schemas.docs import MaterialBrief, SiteBrief, StoreBrief, mat_brief
from app.services.common import audit, bump, check_version, ensure_status, next_doc_no, now_utc, q2, q3, snapshot, today
from app.services.context import UserContext, ensure_covers, scope_filter
from app.services.lookups import site_store
from app.services.names import user_names
from app.services.notify import notify, notify_users
from app.services.stock_engine import Movement, available, post_movements

router = APIRouter(prefix="/issue-notes", tags=["issue notes"])

ZERO = Decimal("0")


class IssueItemIn(In):
    request_item_id: uuid.UUID
    issued_qty: PosQty


class IssueIn(In):
    request_id: uuid.UUID
    issue_date: date | None = None
    vehicle_no: Annotated[str | None, Field(max_length=20)] = None
    driver_name: Annotated[str | None, Field(max_length=80)] = None
    driver_phone: Annotated[str | None, Field(max_length=20)] = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[IssueItemIn], Field(min_length=1, max_length=300)]


class IssueUpdateIn(In):
    issue_date: date | None = None
    vehicle_no: Annotated[str | None, Field(max_length=20)] = None
    driver_name: Annotated[str | None, Field(max_length=80)] = None
    driver_phone: Annotated[str | None, Field(max_length=20)] = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[IssueItemIn], Field(min_length=1, max_length=300)]
    version: int


class ReceiveLineIn(In):
    item_id: uuid.UUID
    received_qty: NonNegQty
    shortage_qty: NonNegQty = Decimal("0")
    damaged_qty: NonNegQty = Decimal("0")


class ReceiveIn(In):
    items: list[ReceiveLineIn] = Field([], description="Omit to confirm every line received in full")
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    version: int | None = None


class IssueItemOut(Out):
    id: uuid.UUID
    line_no: int
    request_item_id: uuid.UUID
    material: MaterialBrief
    issued_qty: Num
    approved_qty: OptNum
    rate: OptNum
    amount: OptNum
    received_qty: OptNum
    shortage_qty: Num
    damaged_qty: Num
    central_stock: Num


class IssueSummaryOut(Out):
    id: uuid.UUID
    issue_no: str
    status: str
    version: int
    request_id: uuid.UUID
    request_no: str
    project_id: uuid.UUID
    project_name: str
    site: SiteBrief
    from_store: StoreBrief
    to_store: StoreBrief
    issue_date: date
    vehicle_no: str | None
    driver_name: str | None
    driver_phone: str | None
    remarks: str | None
    total_value: OptNum
    item_count: int
    created_by_id: uuid.UUID | None
    created_by_name: str | None = None
    issued_by_name: str | None = None
    issued_at: datetime | None
    received_by_name: str | None = None
    received_at: datetime | None
    receipt_remarks: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime


class IssueOut(IssueSummaryOut):
    items: list[IssueItemOut]


async def _stores(db, ids) -> dict[uuid.UUID, Store]:
    return {s.id: s for s in (await db.execute(select(Store).where(Store.id.in_(set(ids))))).scalars()}


def _summary(n: IssueNote, names: dict, stores: dict) -> IssueSummaryOut:
    fs, ts = stores[n.from_store_id], stores[n.to_store_id]
    return IssueSummaryOut(
        id=n.id,
        issue_no=n.issue_no,
        status=n.status,
        version=n.version,
        request_id=n.request_id,
        request_no=n.request.request_no,
        project_id=n.project_id,
        project_name=n.project.name,
        site=SiteBrief(id=n.site.id, code=n.site.code, name=n.site.name, project_id=n.site.project_id, project_name=n.project.name),
        from_store=StoreBrief(id=fs.id, code=fs.code, name=fs.name),
        to_store=StoreBrief(id=ts.id, code=ts.code, name=ts.name),
        issue_date=n.issue_date,
        vehicle_no=n.vehicle_no,
        driver_name=n.driver_name,
        driver_phone=n.driver_phone,
        remarks=n.remarks,
        total_value=n.total_value,
        item_count=len(n.items),
        created_by_id=n.created_by_id,
        created_by_name=names.get(n.created_by_id),
        issued_by_name=names.get(n.issued_by_id),
        issued_at=n.issued_at,
        received_by_name=names.get(n.received_by_id),
        received_at=n.received_at,
        receipt_remarks=n.receipt_remarks,
        cancelled_at=n.cancelled_at,
        cancel_reason=n.cancel_reason,
        created_at=n.created_at,
        updated_at=n.updated_at,
    )


async def _full(db, note_id: uuid.UUID) -> IssueOut:
    n = await get_or_404(db, IssueNote, note_id, "Issue note")
    names = await user_names(db, [n.created_by_id, n.issued_by_id, n.received_by_id])
    stores = await _stores(db, [n.from_store_id, n.to_store_id])
    approved = {ri.id: ri.approved_qty for ri in n.request.items}
    stock = await available(db, n.from_store_id, [i.material_id for i in n.items])
    return IssueOut(
        **_summary(n, names, stores).model_dump(),
        items=[
            IssueItemOut(
                id=i.id,
                line_no=i.line_no,
                request_item_id=i.request_item_id,
                material=mat_brief(i.material),
                issued_qty=i.issued_qty,
                approved_qty=approved.get(i.request_item_id),
                rate=i.rate,
                amount=i.amount,
                received_qty=i.received_qty,
                shortage_qty=i.shortage_qty,
                damaged_qty=i.damaged_qty,
                central_stock=stock.get(i.material_id, ZERO),
            )
            for i in n.items
        ],
    )


def _covers_note(ctx: UserContext, perm: str, n: IssueNote) -> None:
    ensure_covers(ctx, perm, "Issue note", project_id=n.project_id, site_id=n.site_id, store_ids=(n.from_store_id, n.to_store_id))


async def _committed_qty(db, request_item_ids: list[uuid.UUID], exclude_note_id: uuid.UUID | None = None) -> dict[uuid.UUID, Decimal]:
    """Qty already on non-cancelled issue notes (drafts included, so two drafts can't over-issue)."""
    stmt = (
        select(IssueNoteItem.request_item_id, func.sum(IssueNoteItem.issued_qty))
        .join(IssueNote, IssueNote.id == IssueNoteItem.issue_note_id)
        .where(IssueNoteItem.request_item_id.in_(request_item_ids), IssueNote.status != IssueStatus.CANCELLED)
        .group_by(IssueNoteItem.request_item_id)
    )
    if exclude_note_id:
        stmt = stmt.where(IssueNote.id != exclude_note_id)
    return {k: Decimal(v or 0) for k, v in await db.execute(stmt)}


async def _build_items(db, r: MaterialRequest, items: list[IssueItemIn], exclude_note_id: uuid.UUID | None = None) -> list[IssueNoteItem]:
    by_id = {i.id: i for i in r.items}
    if len({i.request_item_id for i in items}) != len(items):
        raise AppError("DUPLICATE_LINE", "Each request line may appear only once", 422)
    committed = await _committed_qty(db, list(by_id), exclude_note_id)
    out = []
    for n, it in enumerate(items, start=1):
        ri = by_id.get(it.request_item_id)
        if ri is None:
            raise AppError("VALIDATION_ERROR", "Line does not belong to this request", 422)
        remaining = Decimal(ri.approved_qty or 0) - committed.get(ri.id, ZERO)
        if q3(it.issued_qty) > remaining:
            raise AppError(
                "EXCEEDS_APPROVED",
                f"{ri.material.name}: only {max(remaining, ZERO).normalize():f} {ri.material.base_uom.code} left to issue on this request",
                422,
                {"request_item_id": str(ri.id), "remaining": float(max(remaining, ZERO))},
            )
        out.append(
            IssueNoteItem(id=uuid.uuid4(), line_no=n, request_item_id=ri.id, material_id=ri.material_id, issued_qty=q3(it.issued_qty))
        )
    return out


def _refresh_rfq_status(r: MaterialRequest) -> None:
    if r.status in (RfqStatus.CLOSED, RfqStatus.CANCELLED):
        return
    issued_any = any(Decimal(i.issued_qty) > 0 for i in r.items)
    fully = all(Decimal(i.issued_qty) >= Decimal(i.approved_qty or 0) for i in r.items)
    r.status = RfqStatus.ISSUED if fully else (RfqStatus.PARTIALLY_ISSUED if issued_any else RfqStatus.STORE_APPROVED)


# ------------------------------------------------------------------ endpoints


@router.get("", response_model=PageEnvelope[IssueSummaryOut])
async def list_issue_notes(
    db: DB,
    ctx: Perm("issue.read"),
    paging: PagingDep,
    status: IssueStatus | None = None,
    queue: Annotated[
        str | None, Query(pattern="^(post|receive)$", description="post: drafts I can issue · receive: in transit to my sites")
    ] = None,
    project_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
):
    N = IssueNote
    stmt = select(N).where(
        scope_filter(ctx.scope("issue.read"), store_cols=(N.from_store_id, N.to_store_id), site_col=N.site_id, project_col=N.project_id)
    )
    if queue == "post":
        stmt = stmt.where(N.status == IssueStatus.DRAFT, scope_filter(ctx.scope("issue.post"), store_cols=(N.from_store_id,)))
    elif queue == "receive":
        stmt = stmt.where(
            N.status == IssueStatus.ISSUED,
            scope_filter(ctx.scope("issue.receive"), store_cols=(N.to_store_id,), site_col=N.site_id, project_col=N.project_id),
        )
    for col, val in ((N.status, status), (N.project_id, project_id), (N.site_id, site_id), (N.request_id, request_id)):
        if val is not None:
            stmt = stmt.where(col == val)
    if date_from:
        stmt = stmt.where(N.issue_date >= date_from)
    if date_to:
        stmt = stmt.where(N.issue_date <= date_to)
    if q:
        stmt = stmt.where(or_(N.issue_no.ilike(f"%{q}%"), N.vehicle_no.ilike(f"%{q}%")))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(N.created_at.desc()).offset(paging.offset).limit(paging.limit))).unique().scalars().all()
    names = await user_names(db, [x for n in rows for x in (n.created_by_id, n.issued_by_id, n.received_by_id)])
    stores = await _stores(db, [x for n in rows for x in (n.from_store_id, n.to_store_id)])
    return respond_page(ctx, [_summary(n, names, stores) for n in rows], total, paging)


async def create_note(db, ctx: UserContext, r: MaterialRequest, body: IssueIn) -> IssueNote:
    """Creates a DRAFT issue note against a locked, approved request."""
    ensure_status(r, RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED, action="issue against this request")
    issue_date = body.issue_date or today()
    if issue_date > today():
        raise AppError("VALIDATION_ERROR", "Issue date cannot be in the future", 422)
    to_store = await site_store(db, r.site_id)
    n = IssueNote(
        id=uuid.uuid4(),
        issue_no=await next_doc_no(db, DocType.ISSUE, issue_date),
        request_id=r.id,
        from_store_id=r.store_id,
        to_store_id=to_store.id,
        project_id=r.project_id,
        site_id=r.site_id,
        issue_date=issue_date,
        vehicle_no=body.vehicle_no,
        driver_name=body.driver_name,
        driver_phone=body.driver_phone,
        remarks=body.remarks,
        status=IssueStatus.DRAFT,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    n.items = await _build_items(db, r, body.items)
    db.add(n)
    await db.flush()
    await audit(db, ctx, "CREATE", "issue_note", n.id, f"{n.issue_no} for {r.request_no}", None, snapshot(n))
    return n


@router.post("", response_model=Envelope[IssueOut], status_code=201)
async def create_issue_note(body: IssueIn, db: DB, ctx: Perm("issue.create"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    r = await get_or_404(db, MaterialRequest, body.request_id, "Request", lock=True)
    ensure_covers(ctx, "issue.create", "Request", store_ids=(r.store_id,))
    n = await create_note(db, ctx, r, body)
    return await finish(db, ctx, lambda: _full(db, n.id), idem)


@router.get("/{note_id}", response_model=Envelope[IssueOut])
async def get_issue_note(note_id: uuid.UUID, db: DB, ctx: Perm("issue.read")):
    n = await get_or_404(db, IssueNote, note_id, "Issue note")
    _covers_note(ctx, "issue.read", n)
    return respond(ctx, await _full(db, note_id))


@router.put("/{note_id}", response_model=Envelope[IssueOut])
async def update_issue_note(note_id: uuid.UUID, body: IssueUpdateIn, db: DB, ctx: Perm("issue.create")):
    n = await get_or_404(db, IssueNote, note_id, "Issue note", lock=True)
    ensure_covers(ctx, "issue.create", "Issue note", store_ids=(n.from_store_id,))
    ensure_status(n, IssueStatus.DRAFT, action="edit")
    check_version(n, body.version)
    r = await get_or_404(db, MaterialRequest, n.request_id, "Request", lock=True)
    before = snapshot(n)
    for k in ("vehicle_no", "driver_name", "driver_phone", "remarks"):
        setattr(n, k, getattr(body, k))
    n.issue_date = body.issue_date or n.issue_date
    n.items.clear()
    await db.flush()
    n.items.extend(await _build_items(db, r, body.items, exclude_note_id=n.id))
    bump(n, ctx)
    await db.flush()
    await audit(db, ctx, "UPDATE", "issue_note", n.id, n.issue_no, before, snapshot(n))
    return await finish(db, ctx, lambda: _full(db, n.id))


async def dispatch_note(db, ctx: UserContext, n: IssueNote) -> None:
    """Posts a DRAFT note: central stock −, request quantities updated, site notified."""
    r = await get_or_404(db, MaterialRequest, n.request_id, "Request", lock=True)
    ensure_status(r, RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED, action="issue against this request")
    req_items = {i.id: i for i in r.items}
    for it in n.items:
        ri = req_items[it.request_item_id]
        if Decimal(ri.issued_qty) + Decimal(it.issued_qty) > Decimal(ri.approved_qty or 0):
            raise AppError("EXCEEDS_APPROVED", f"{ri.material.name}: issue would exceed approved quantity", 409)

    posted = await post_movements(
        db,
        [
            Movement(
                store_id=n.from_store_id,
                material_id=it.material_id,
                qty=Decimal(it.issued_qty),
                inward=False,
                txn_type=TxnType.ISSUE,
                ref_type="issue_note",
                ref_id=n.id,
                ref_no=n.issue_no,
                txn_date=n.issue_date,
            )
            for it in n.items
        ],
        ctx.user_id,
    )
    for it, p in zip(n.items, posted, strict=True):
        it.rate, it.amount = p.rate, q2(p.value)
        req_items[it.request_item_id].issued_qty = Decimal(req_items[it.request_item_id].issued_qty) + Decimal(it.issued_qty)
    n.total_value = q2(sum((Decimal(i.amount) for i in n.items), ZERO))
    n.status, n.issued_by_id, n.issued_at = IssueStatus.ISSUED, ctx.user_id, now_utc()
    r.first_issued_at = r.first_issued_at or n.issued_at
    _refresh_rfq_status(r)
    bump(n, ctx)
    bump(r, ctx)
    await audit(db, ctx, "ISSUE", "issue_note", n.id, f"{n.issue_no} issued to {n.site.name}")
    await notify(
        db,
        "issue.receive",
        type="MATERIAL_DISPATCHED",
        title=f"{n.issue_no}: material on the way",
        body=f"{len(n.items)} item(s) · vehicle {n.vehicle_no or '—'}",
        entity_type="issue_note",
        entity_id=n.id,
        exclude=ctx.user_id,
        site_id=n.site_id,
        project_id=n.project_id,
    )


@router.post("/{note_id}/issue", response_model=Envelope[IssueOut], summary="Issue → central stock − (in transit to site)")
async def post_issue(note_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("issue.post")):
    n = await get_or_404(db, IssueNote, note_id, "Issue note", lock=True)
    ensure_covers(ctx, "issue.post", "Issue note", store_ids=(n.from_store_id,))
    ensure_status(n, IssueStatus.DRAFT, action="issue")
    check_version(n, body.version)
    await dispatch_note(db, ctx, n)
    return await finish(db, ctx, lambda: _full(db, n.id))


@router.post("/{note_id}/receive", response_model=Envelope[IssueOut], summary="Site acknowledges receipt → site stock +")
async def receive_issue(note_id: uuid.UUID, body: ReceiveIn, db: DB, ctx: Perm("issue.receive")):
    n = await get_or_404(db, IssueNote, note_id, "Issue note", lock=True)
    ensure_covers(ctx, "issue.receive", "Issue note", site_id=n.site_id, project_id=n.project_id, store_ids=(n.to_store_id,))
    ensure_status(n, IssueStatus.ISSUED, action="receive")
    check_version(n, body.version)
    lines = {ln.item_id: ln for ln in body.items}
    if set(lines) - {i.id for i in n.items}:
        raise AppError("VALIDATION_ERROR", "Unknown issue line", 422)
    loss = False
    for it in n.items:
        ln = lines.get(it.id)
        if ln is None:
            it.received_qty, it.shortage_qty, it.damaged_qty = it.issued_qty, ZERO, ZERO
            continue
        total = q3(ln.received_qty + ln.shortage_qty + ln.damaged_qty)
        if total != q3(Decimal(it.issued_qty)):
            raise AppError(
                "RECEIPT_MISMATCH",
                f"{it.material.name}: received + shortage + damaged must equal issued ({Decimal(it.issued_qty).normalize():f})",
                422,
            )
        it.received_qty, it.shortage_qty, it.damaged_qty = q3(ln.received_qty), q3(ln.shortage_qty), q3(ln.damaged_qty)
        loss = loss or it.shortage_qty > 0 or it.damaged_qty > 0
    await post_movements(
        db,
        [
            Movement(
                store_id=n.to_store_id,
                material_id=it.material_id,
                qty=Decimal(it.received_qty),
                inward=True,
                rate=Decimal(it.rate),
                txn_type=TxnType.RECEIPT,
                ref_type="issue_note",
                ref_id=n.id,
                ref_no=n.issue_no,
                txn_date=today(),
            )
            for it in n.items
            if Decimal(it.received_qty) > 0
        ],
        ctx.user_id,
    )
    n.status = IssueStatus.PARTIALLY_RECEIVED if loss else IssueStatus.RECEIVED
    n.received_by_id, n.received_at, n.receipt_remarks = ctx.user_id, now_utc(), body.remarks
    bump(n, ctx)
    await audit(db, ctx, "RECEIVE", "issue_note", n.id, f"{n.issue_no} received" + (" with shortage/damage" if loss else ""))
    if loss:
        await notify(
            db,
            "issue.post",
            type="TRANSIT_LOSS",
            title=f"{n.issue_no} received with shortage/damage",
            body=body.remarks,
            entity_type="issue_note",
            entity_id=n.id,
            exclude=ctx.user_id,
            store_id=n.from_store_id,
        )
    return await finish(db, ctx, lambda: _full(db, n.id))


@router.post("/{note_id}/cancel", response_model=Envelope[IssueOut], summary="Cancel a draft, or reverse an issued (not yet received) note")
async def cancel_issue(note_id: uuid.UUID, body: CancelIn, db: DB, ctx: Perm("issue.create", "issue.post")):
    n = await get_or_404(db, IssueNote, note_id, "Issue note", lock=True)
    ensure_status(n, IssueStatus.DRAFT, IssueStatus.ISSUED, action="cancel")
    check_version(n, body.version)
    if n.status == IssueStatus.ISSUED:
        ensure_covers(ctx, "issue.post", "Issue note", store_ids=(n.from_store_id,))
        r = await get_or_404(db, MaterialRequest, n.request_id, "Request", lock=True)
        req_items = {i.id: i for i in r.items}
        await post_movements(
            db,
            [
                Movement(
                    store_id=n.from_store_id,
                    material_id=it.material_id,
                    qty=Decimal(it.issued_qty),
                    inward=True,
                    rate=Decimal(it.rate),
                    txn_type=TxnType.REVERSAL,
                    ref_type="issue_note",
                    ref_id=n.id,
                    ref_no=n.issue_no,
                    txn_date=today(),
                )
                for it in n.items
            ],
            ctx.user_id,
        )
        for it in n.items:
            ri: MaterialRequestItem = req_items[it.request_item_id]
            ri.issued_qty = max(Decimal(ri.issued_qty) - Decimal(it.issued_qty), ZERO)
        _refresh_rfq_status(r)
        bump(r, ctx)
        notify_users(
            db,
            {n.received_by_id, n.created_by_id},
            type="ISSUE_CANCELLED",
            title=f"Issue note {n.issue_no} was cancelled",
            body=body.reason,
            entity_type="issue_note",
            entity_id=n.id,
            exclude=ctx.user_id,
        )
    else:
        if not (ctx.covers("issue.create", store_ids=(n.from_store_id,)) or ctx.covers("issue.post", store_ids=(n.from_store_id,))):
            raise AppError("NOT_FOUND", "Issue note not found", 404)
    n.status, n.cancelled_by_id, n.cancelled_at, n.cancel_reason = IssueStatus.CANCELLED, ctx.user_id, now_utc(), body.reason
    bump(n, ctx)
    await audit(db, ctx, "CANCEL", "issue_note", n.id, body.reason)
    return await finish(db, ctx, lambda: _full(db, n.id))


@router.get("/{note_id}/pdf", summary="Issue note + gate pass (PDF, with QR code)", response_class=Response)
async def issue_pdf(note_id: uuid.UUID, db: DB, ctx: Perm("issue.read")):
    from app.services.pdf import issue_note_pdf

    n = await get_or_404(db, IssueNote, note_id, "Issue note")
    _covers_note(ctx, "issue.read", n)
    data = await _full(db, note_id)
    pdf = issue_note_pdf(data, show_finance=ctx.show_finance)
    filename = data.issue_no.replace("/", "-") + ".pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{filename}"'})
