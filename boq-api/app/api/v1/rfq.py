"""RFQ / Material Request: a site asks the central store for material.

Workflow:  DRAFT → SUBMITTED → STORE_APPROVED (PM_APPROVED is legacy, kept for old data) → PARTIALLY_ISSUED → ISSUED
                      ↘ RETURNED (edit & resubmit)  ↘ REJECTED            ↘ CLOSED / CANCELLED
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, or_, select

from app.api.deps import DB, IdemDep, PagingDep, Perm, finish, get_or_404, respond, respond_page
from app.api.v1.issue import IssueIn, IssueItemIn, IssueOut, create_note, dispatch_note
from app.api.v1.issue import _full as issue_full
from app.core.errors import AppError, forbidden
from app.core.responses import Envelope, PageEnvelope
from app.models import IssueNote, MaterialRequest, MaterialRequestItem, Site
from app.models.enums import ApprovalAction, DocType, IssueStatus, Priority, RfqStatus
from app.schemas.common import ApprovalOut, CommentIn, In, Num, OptNum, Out, PosQty, VersionIn
from app.schemas.docs import MaterialBrief, SiteBrief, StoreBrief, mat_brief
from app.services.boq import project_allocation_usage
from app.services.common import (
    approvals_for,
    audit,
    bump,
    check_version,
    ensure_status,
    financial_year,
    next_doc_no,
    now_utc,
    q3,
    record_approval,
    snapshot,
    to_base_uom,
    today,
)
from app.services.context import UserContext, ensure_covers, scope_filter
from app.services.lookups import central_store, ensure_unique_materials, get_site, materials_by_id
from app.services.names import user_names
from app.services.notify import notify, notify_users
from app.services.stock_engine import available

router = APIRouter(prefix="/rfqs", tags=["RFQ (material request)"])


class RfqItemIn(In):
    material_id: uuid.UUID
    requested_qty: PosQty
    uom_id: uuid.UUID | None = None
    remarks: Annotated[str | None, Field(max_length=500)] = None


class RfqIn(In):
    site_id: uuid.UUID
    required_by: date | None = None
    priority: Priority = Priority.NORMAL
    purpose: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[RfqItemIn], Field(min_length=1, max_length=300)]
    submit: bool = Field(False, description="Create and submit in one call (mobile)")


class RfqUpdateIn(In):
    required_by: date | None = None
    priority: Priority = Priority.NORMAL
    purpose: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[RfqItemIn], Field(min_length=1, max_length=300)]
    version: int


class ApproveLineIn(In):
    item_id: uuid.UUID
    approved_qty: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)]


class ApproveIn(In):
    items: list[ApproveLineIn] = Field([], description="Omit to approve every line at its current quantity")
    comment: Annotated[str | None, Field(max_length=1000)] = None
    override_allocation: bool = False
    version: int | None = None


class RfqItemOut(Out):
    id: uuid.UUID
    line_no: int
    material: MaterialBrief
    requested_qty: Num
    approved_qty: OptNum
    issued_qty: Num
    pending_issue_qty: Num
    central_stock: Num
    remarks: str | None


class IssueRef(Out):
    id: uuid.UUID
    issue_no: str
    status: str
    issue_date: date


class RfqSummaryOut(Out):
    id: uuid.UUID
    request_no: str
    status: str
    version: int
    priority: str
    project_id: uuid.UUID
    project_name: str
    site: SiteBrief
    store: StoreBrief
    request_date: date
    required_by: date | None
    purpose: str | None
    item_count: int
    submitted_at: datetime | None
    first_issued_at: datetime | None
    created_by_id: uuid.UUID | None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


class RfqOut(RfqSummaryOut):
    items: list[RfqItemOut]
    approvals: list[ApprovalOut]
    issue_notes: list[IssueRef]


class AllocationCheckLine(Out):
    material_id: uuid.UUID
    material_name: str
    has_allocation: bool
    allocated: Num
    approved_elsewhere: Num
    issued: Num
    this_request: Num
    remaining_after: OptNum
    exceeds: bool


def _summary(r: MaterialRequest, names: dict) -> RfqSummaryOut:
    return RfqSummaryOut(
        id=r.id,
        request_no=r.request_no,
        status=r.status,
        version=r.version,
        priority=r.priority,
        project_id=r.project_id,
        project_name=r.project.name,
        site=SiteBrief(id=r.site.id, code=r.site.code, name=r.site.name, project_id=r.site.project_id, project_name=r.project.name),
        store=StoreBrief(id=r.store.id, code=r.store.code, name=r.store.name),
        request_date=r.request_date,
        required_by=r.required_by,
        purpose=r.purpose,
        item_count=len(r.items),
        submitted_at=r.submitted_at,
        first_issued_at=r.first_issued_at,
        created_by_id=r.created_by_id,
        created_by_name=names.get(r.created_by_id),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


async def _full(db, rfq_id: uuid.UUID) -> RfqOut:
    r = await get_or_404(db, MaterialRequest, rfq_id, "Request")
    approvals = await approvals_for(db, "rfq", r.id)
    names = await user_names(db, [r.created_by_id, *[a.actor_id for a in approvals]])
    stock = await available(db, r.store_id, [i.material_id for i in r.items])
    issues = (
        (await db.execute(select(IssueNote).where(IssueNote.request_id == r.id).order_by(IssueNote.created_at))).unique().scalars().all()
    )
    return RfqOut(
        **_summary(r, names).model_dump(),
        items=[
            RfqItemOut(
                id=i.id,
                line_no=i.line_no,
                material=mat_brief(i.material),
                requested_qty=i.requested_qty,
                approved_qty=i.approved_qty,
                issued_qty=i.issued_qty,
                pending_issue_qty=max(Decimal(i.approved_qty or 0) - Decimal(i.issued_qty), Decimal(0)),
                central_stock=stock.get(i.material_id, Decimal(0)),
                remarks=i.remarks,
            )
            for i in r.items
        ],
        approvals=[
            ApprovalOut(
                stage=a.stage,
                action=a.action,
                actor_id=a.actor_id,
                actor_name=names.get(a.actor_id),
                comment=a.comment,
                acted_at=a.acted_at,
            )
            for a in approvals
        ],
        issue_notes=[IssueRef(id=n.id, issue_no=n.issue_no, status=n.status, issue_date=n.issue_date) for n in issues],
    )


async def _load_for(db, ctx: UserContext, rfq_id: uuid.UUID, perm: str, lock: bool = False) -> MaterialRequest:
    r = await get_or_404(db, MaterialRequest, rfq_id, "Request", lock=lock)
    ensure_covers(ctx, perm, "Request", project_id=r.project_id, site_id=r.site_id, store_ids=(r.store_id,))
    return r


async def _build_items(db, items: list[RfqItemIn]) -> list[MaterialRequestItem]:
    ensure_unique_materials(items)
    mats = await materials_by_id(db, [i.material_id for i in items])
    out = []
    for n, i in enumerate(items, start=1):
        qty, _ = await to_base_uom(db, mats[i.material_id], i.requested_qty, None, i.uom_id)
        out.append(MaterialRequestItem(id=uuid.uuid4(), line_no=n, material_id=i.material_id, requested_qty=qty, remarks=i.remarks))
    return out


async def _submit(db, ctx: UserContext, r: MaterialRequest) -> None:
    r.status, r.submitted_at = RfqStatus.SUBMITTED, now_utc()
    await record_approval(db, ctx, "rfq", r.id, "SITE", ApprovalAction.SUBMITTED, None)
    await audit(db, ctx, "SUBMIT", "rfq", r.id, r.request_no)
    await notify(
        db,
        "rfq.approve_store",
        type="RFQ_SUBMITTED",
        title=f"Request {r.request_no} needs your approval",
        body=f"{r.site.name if r.site else ''} · {len(r.items)} item(s) · {r.priority}",
        entity_type="rfq",
        entity_id=r.id,
        exclude=ctx.user_id,
        store_id=r.store_id,
    )


def _apply_approved_qty(r: MaterialRequest, lines: list[ApproveLineIn], *, cap_to_current: bool) -> None:
    by_id = {i.id: i for i in r.items}
    given = {ln.item_id: ln.approved_qty for ln in lines}
    unknown = set(given) - set(by_id)
    if unknown:
        raise AppError("VALIDATION_ERROR", "Unknown request line in approval", 422)
    for i in r.items:
        current = Decimal(i.approved_qty) if (cap_to_current and i.approved_qty is not None) else Decimal(i.requested_qty)
        qty = q3(given.get(i.id, current))
        if qty > current:
            raise AppError(
                "APPROVED_EXCEEDS_LIMIT",
                f"Approved quantity for {i.material.name} cannot exceed {current.normalize():f}",
                422,
            )
        i.approved_qty = qty
    if all(Decimal(i.approved_qty) == 0 for i in r.items):
        raise AppError("VALIDATION_ERROR", "Approve at least one line with quantity > 0, or reject the request", 422)


async def _allocation_check(db, r: MaterialRequest) -> list[AllocationCheckLine]:
    fy = financial_year(r.request_date)
    usage = await project_allocation_usage(db, fy, r.project_id, [i.material_id for i in r.items], exclude_request_id=r.id)
    out = []
    for i in r.items:
        u = usage[i.material_id]
        this = Decimal(i.approved_qty if i.approved_qty is not None else i.requested_qty)
        remaining = u["allocated"] - u["approved"] - this if u["has_allocation"] else None
        out.append(
            AllocationCheckLine(
                material_id=i.material_id,
                material_name=i.material.name,
                has_allocation=u["has_allocation"],
                allocated=u["allocated"],
                approved_elsewhere=u["approved"],
                issued=u["issued"],
                this_request=this,
                remaining_after=remaining,
                exceeds=remaining is not None and remaining < 0,
            )
        )
    return out


# ------------------------------------------------------------------ endpoints


@router.get("", response_model=PageEnvelope[RfqSummaryOut])
async def list_rfqs(
    db: DB,
    ctx: Perm("rfq.read"),
    paging: PagingDep,
    status: RfqStatus | None = None,
    queue: Annotated[str | None, Query(pattern="^(store|issue|mine)$", description="Work queues: store, issue, mine")] = None,
    project_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    priority: Priority | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
):
    M = MaterialRequest
    stmt = select(M).where(scope_filter(ctx.scope("rfq.read"), store_cols=(M.store_id,), site_col=M.site_id, project_col=M.project_id))
    if queue == "store":
        stmt = stmt.where(
            M.status.in_([RfqStatus.SUBMITTED, RfqStatus.PM_APPROVED]),
            scope_filter(ctx.scope("rfq.approve_store"), store_cols=(M.store_id,)),
        )
    elif queue == "issue":
        stmt = stmt.where(
            M.status.in_([RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED]),
            scope_filter(ctx.scope("issue.create"), store_cols=(M.store_id,)),
        )
    elif queue == "mine":
        stmt = stmt.where(M.created_by_id == ctx.user_id)
    if status:
        stmt = stmt.where(M.status == status)
    if project_id:
        stmt = stmt.where(M.project_id == project_id)
    if site_id:
        stmt = stmt.where(M.site_id == site_id)
    if priority:
        stmt = stmt.where(M.priority == priority)
    if date_from:
        stmt = stmt.where(M.request_date >= date_from)
    if date_to:
        stmt = stmt.where(M.request_date <= date_to)
    if q:
        stmt = stmt.where(or_(M.request_no.ilike(f"%{q}%"), M.purpose.ilike(f"%{q}%")))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    order = (M.priority == Priority.URGENT).desc(), M.created_at.desc()
    rows = (await db.execute(stmt.order_by(*order).offset(paging.offset).limit(paging.limit))).unique().scalars().all()
    names = await user_names(db, [r.created_by_id for r in rows])
    return respond_page(ctx, [_summary(r, names) for r in rows], total, paging)


@router.post("", response_model=Envelope[RfqOut], status_code=201)
async def create_rfq(body: RfqIn, db: DB, ctx: Perm("rfq.create"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    site: Site = await get_site(db, body.site_id)
    ensure_covers(ctx, "rfq.create", "Site", site_id=site.id, project_id=site.project_id)
    store = await central_store(db)
    r = MaterialRequest(
        id=uuid.uuid4(),
        request_no=await next_doc_no(db, DocType.RFQ),
        project_id=site.project_id,
        site_id=site.id,
        store_id=store.id,
        request_date=today(),
        required_by=body.required_by,
        priority=body.priority,
        purpose=body.purpose,
        status=RfqStatus.DRAFT,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    if body.required_by and body.required_by < today():
        raise AppError("VALIDATION_ERROR", "Required-by date cannot be in the past", 422)
    r.items = await _build_items(db, body.items)
    db.add(r)
    await db.flush()
    await audit(db, ctx, "CREATE", "rfq", r.id, r.request_no, None, snapshot(r))
    if body.submit:
        r.site = site
        await _submit(db, ctx, r)
    return await finish(db, ctx, lambda: _full(db, r.id), idem)


@router.get("/{rfq_id}", response_model=Envelope[RfqOut])
async def get_rfq(rfq_id: uuid.UUID, db: DB, ctx: Perm("rfq.read")):
    await _load_for(db, ctx, rfq_id, "rfq.read")
    return respond(ctx, await _full(db, rfq_id))


@router.get(
    "/{rfq_id}/allocation-check", response_model=Envelope[list[AllocationCheckLine]], summary="BOQ project allocation vs this request"
)
async def allocation_check(rfq_id: uuid.UUID, db: DB, ctx: Perm("rfq.read")):
    r = await _load_for(db, ctx, rfq_id, "rfq.read")
    return respond(ctx, await _allocation_check(db, r))


@router.put("/{rfq_id}", response_model=Envelope[RfqOut])
async def update_rfq(rfq_id: uuid.UUID, body: RfqUpdateIn, db: DB, ctx: Perm("rfq.create")):
    r = await _load_for(db, ctx, rfq_id, "rfq.create", lock=True)
    ensure_status(r, RfqStatus.DRAFT, RfqStatus.RETURNED, action="edit")
    check_version(r, body.version)
    before = snapshot(r)
    r.required_by, r.priority, r.purpose = body.required_by, body.priority, body.purpose
    r.items.clear()
    await db.flush()
    r.items.extend(await _build_items(db, body.items))
    bump(r, ctx)
    await db.flush()
    await audit(db, ctx, "UPDATE", "rfq", r.id, r.request_no, before, snapshot(r))
    return await finish(db, ctx, lambda: _full(db, r.id))


@router.post("/{rfq_id}/submit", response_model=Envelope[RfqOut])
async def submit_rfq(rfq_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("rfq.create")):
    r = await _load_for(db, ctx, rfq_id, "rfq.create", lock=True)
    ensure_status(r, RfqStatus.DRAFT, RfqStatus.RETURNED, action="submit")
    check_version(r, body.version)
    for i in r.items:
        i.approved_qty = None
    await _submit(db, ctx, r)
    bump(r, ctx)
    return await finish(db, ctx, lambda: _full(db, r.id))


async def store_approve_core(db, ctx: UserContext, r: MaterialRequest, body: ApproveIn) -> bool:
    """Store Manager approval of a locked, in-scope request. Returns True when the allocation was overridden."""
    ensure_status(r, RfqStatus.SUBMITTED, RfqStatus.PM_APPROVED, action="approve")
    check_version(r, body.version)
    _apply_approved_qty(r, body.items, cap_to_current=r.status == RfqStatus.PM_APPROVED)
    check = await _allocation_check(db, r)
    exceeded = [c for c in check if c.exceeds]
    if exceeded and not body.override_allocation:
        raise AppError(
            "ALLOCATION_EXCEEDED",
            "This request exceeds the project's BOQ allocation. Re-submit with override_allocation=true to approve anyway.",
            409,
            [c.model_dump(mode="json") for c in exceeded],
        )
    r.status = RfqStatus.STORE_APPROVED
    bump(r, ctx)
    comment = body.comment
    if exceeded:
        comment = f"[Allocation override] {comment or ''}".strip()
    await record_approval(db, ctx, "rfq", r.id, "STORE", ApprovalAction.APPROVED, comment)
    await audit(db, ctx, "STORE_APPROVE", "rfq", r.id, r.request_no + (" (allocation override)" if exceeded else ""))
    notify_users(
        db,
        {r.created_by_id},
        type="RFQ_APPROVED",
        title=f"Your request {r.request_no} was approved",
        entity_type="rfq",
        entity_id=r.id,
        exclude=ctx.user_id,
    )
    return bool(exceeded)


@router.post(
    "/{rfq_id}/store-approve",
    response_model=Envelope[RfqOut],
    summary="Store Manager approval (can reduce quantities); checks BOQ project allocation",
)
async def store_approve(rfq_id: uuid.UUID, body: ApproveIn, db: DB, ctx: Perm("rfq.approve_store")):
    r = await _load_for(db, ctx, rfq_id, "rfq.approve_store", lock=True)
    await store_approve_core(db, ctx, r, body)
    return await finish(db, ctx, lambda: _full(db, r.id))


class ApproveIssueIn(ApproveIn):
    issue_date: date | None = None
    vehicle_no: Annotated[str | None, Field(max_length=20)] = None
    driver_name: Annotated[str | None, Field(max_length=80)] = None
    driver_phone: Annotated[str | None, Field(max_length=20)] = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None


@router.post(
    "/{rfq_id}/approve-and-issue",
    response_model=Envelope[IssueOut],
    status_code=201,
    summary="One step: Store Manager approves (qty may be reduced) and issues; returns the dispatched issue note",
)
async def approve_and_issue(rfq_id: uuid.UUID, body: ApproveIssueIn, db: DB, ctx: Perm("rfq.approve_store"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    if not ctx.has("issue.post"):
        raise forbidden("You are not allowed to issue material")
    r = await _load_for(db, ctx, rfq_id, "rfq.approve_store", lock=True)
    ensure_covers(ctx, "issue.post", "Request", store_ids=(r.store_id,))
    await store_approve_core(db, ctx, r, body)
    lines = [IssueItemIn(request_item_id=i.id, issued_qty=i.approved_qty) for i in r.items if Decimal(i.approved_qty or 0) > 0]
    note = await create_note(
        db,
        ctx,
        r,
        IssueIn(
            request_id=r.id,
            issue_date=body.issue_date,
            vehicle_no=body.vehicle_no,
            driver_name=body.driver_name,
            driver_phone=body.driver_phone,
            remarks=body.remarks,
            items=lines,
        ),
    )
    await dispatch_note(db, ctx, note)
    return await finish(db, ctx, lambda: issue_full(db, note.id), idem)


async def _decide(db, ctx: UserContext, rfq_id: uuid.UUID, body: CommentIn, action: str) -> dict:
    if not body.comment:
        raise AppError("VALIDATION_ERROR", "A comment is required", 422)
    r = await get_or_404(db, MaterialRequest, rfq_id, "Request", lock=True)
    ensure_status(r, RfqStatus.SUBMITTED, RfqStatus.PM_APPROVED, action=action.lower())
    check_version(r, body.version)
    perm, stage = "rfq.approve_store", "STORE"
    ensure_covers(ctx, perm, "Request", project_id=r.project_id, site_id=r.site_id, store_ids=(r.store_id,))
    r.status = RfqStatus.REJECTED if action == ApprovalAction.REJECTED else RfqStatus.RETURNED
    bump(r, ctx)
    await record_approval(db, ctx, "rfq", r.id, stage, action, body.comment)
    await audit(db, ctx, action, "rfq", r.id, body.comment)
    verb = "rejected" if action == ApprovalAction.REJECTED else "returned for changes"
    notify_users(
        db,
        {r.created_by_id},
        type=f"RFQ_{action}",
        title=f"Request {r.request_no} {verb}",
        body=body.comment,
        entity_type="rfq",
        entity_id=r.id,
    )
    return await finish(db, ctx, lambda: _full(db, r.id))


@router.post("/{rfq_id}/reject", response_model=Envelope[RfqOut])
async def reject_rfq(rfq_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("rfq.approve_store")):
    return await _decide(db, ctx, rfq_id, body, ApprovalAction.REJECTED)


@router.post("/{rfq_id}/return", response_model=Envelope[RfqOut], summary="Send back to the site for changes")
async def return_rfq(rfq_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("rfq.approve_store")):
    return await _decide(db, ctx, rfq_id, body, ApprovalAction.RETURNED)


async def _live_issue_count(db, rfq_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(IssueNote).where(IssueNote.request_id == rfq_id, IssueNote.status != IssueStatus.CANCELLED)
        )
    ).scalar_one()


@router.post("/{rfq_id}/cancel", response_model=Envelope[RfqOut])
async def cancel_rfq(rfq_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("rfq.create", "rfq.approve_store")):
    r = await get_or_404(db, MaterialRequest, rfq_id, "Request", lock=True)
    check_version(r, body.version)
    if r.status in (RfqStatus.DRAFT, RfqStatus.SUBMITTED, RfqStatus.RETURNED) and ctx.covers(
        "rfq.create", site_id=r.site_id, project_id=r.project_id
    ):
        pass
    elif r.status in (RfqStatus.SUBMITTED, RfqStatus.PM_APPROVED, RfqStatus.STORE_APPROVED) and ctx.covers(
        "rfq.approve_store", store_ids=(r.store_id,)
    ):
        if await _live_issue_count(db, r.id):
            raise AppError("HAS_ISSUES", "Cancel or receive the issue notes first, or close the request", 409)
    else:
        if not (ctx.covers("rfq.create", site_id=r.site_id) or ctx.covers("rfq.approve_store", store_ids=(r.store_id,))):
            raise AppError("NOT_FOUND", "Request not found", 404)
        raise forbidden(f"Cannot cancel a request that is {r.status}")
    r.status = RfqStatus.CANCELLED
    bump(r, ctx)
    await record_approval(db, ctx, "rfq", r.id, "CANCEL", ApprovalAction.CANCELLED, body.comment)
    await audit(db, ctx, "CANCEL", "rfq", r.id, body.comment)
    return await finish(db, ctx, lambda: _full(db, r.id))


@router.post("/{rfq_id}/close", response_model=Envelope[RfqOut], summary="Close a partially issued request (no further issues)")
async def close_rfq(rfq_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("rfq.approve_store")):
    r = await _load_for(db, ctx, rfq_id, "rfq.approve_store", lock=True)
    ensure_status(r, RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED, action="close")
    check_version(r, body.version)
    drafts = (
        await db.execute(
            select(func.count()).select_from(IssueNote).where(IssueNote.request_id == r.id, IssueNote.status == IssueStatus.DRAFT)
        )
    ).scalar_one()
    if drafts:
        raise AppError("HAS_DRAFT_ISSUES", "Cancel or issue the draft issue notes first", 409)
    r.status = RfqStatus.CLOSED
    bump(r, ctx)
    await record_approval(db, ctx, "rfq", r.id, "STORE", ApprovalAction.CLOSED, body.comment)
    await audit(db, ctx, "CLOSE", "rfq", r.id, body.comment)
    return await finish(db, ctx, lambda: _full(db, r.id))
