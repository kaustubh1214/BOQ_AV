"""BOQ — the central store master. Management plans quantity + estimated rate per material for the FY."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import func, or_, select

from app.api.deps import DB, IdemDep, PagingDep, Perm, finish, get_or_404, respond, respond_page
from app.api.v1.market import benchmarks
from app.core.errors import AppError
from app.core.responses import Envelope, PageEnvelope
from app.models import Boq, BoqAllocation, BoqItem, Project, Site
from app.models.enums import ApprovalAction, BoqStatus, DocType
from app.schemas.common import ApprovalOut, CommentIn, In, Num, OptNum, Out, PosQty, Rate, VersionIn
from app.schemas.docs import MaterialBrief, mat_brief
from app.services.boq import BOQ_FULL, allocation_progress, boq_progress
from app.services.common import (
    approvals_for,
    audit,
    bump,
    check_version,
    ensure_status,
    financial_year,
    maker_checker,
    next_doc_no,
    now_utc,
    q2,
    record_approval,
    snapshot,
    to_base_uom,
)
from app.services.context import UserContext
from app.services.lookups import ensure_unique_materials, materials_by_id
from app.services.names import user_names
from app.services.notify import notify, notify_users

router = APIRouter(prefix="/boqs", tags=["boq"])

FY = Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")]


class AllocationIn(In):
    project_id: uuid.UUID
    site_id: uuid.UUID | None = None
    allocated_qty: PosQty


class BoqItemIn(In):
    material_id: uuid.UUID
    planned_qty: PosQty
    estimated_rate: Rate
    uom_id: uuid.UUID | None = Field(None, description="Qty/rate entered in this UOM; stored in base UOM")
    remarks: Annotated[str | None, Field(max_length=500)] = None
    allocations: list[AllocationIn] = []


class BoqIn(In):
    title: Annotated[str, Field(min_length=3, max_length=200)]
    financial_year: FY | None = None
    remarks: Annotated[str | None, Field(max_length=2000)] = None
    items: Annotated[list[BoqItemIn], Field(min_length=1, max_length=1000)]


class BoqUpdateIn(BoqIn):
    version: int


class AllocationOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    site_id: uuid.UUID | None
    site_name: str | None
    allocated_qty: Num


class BoqItemOut(Out):
    id: uuid.UUID
    line_no: int
    material: MaterialBrief
    planned_qty: Num
    estimated_rate: OptNum
    estimated_amount: OptNum
    benchmark_rate: OptNum
    remarks: str | None
    allocations: list[AllocationOut]


class BoqSummaryOut(Out):
    id: uuid.UUID
    boq_no: str
    title: str
    financial_year: str
    revision: int
    root_boq_id: uuid.UUID | None
    parent_boq_id: uuid.UUID | None
    status: str
    total_estimated_value: OptNum
    item_count: int = 0
    remarks: str | None
    version: int
    created_by_id: uuid.UUID | None
    created_by_name: str | None = None
    submitted_at: datetime | None
    approved_by_id: uuid.UUID | None
    approved_by_name: str | None = None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BoqOut(BoqSummaryOut):
    items: list[BoqItemOut]
    approvals: list[ApprovalOut]


class ProgressLine(Out):
    material_id: uuid.UUID
    material_code: str
    material_name: str
    uom: str
    planned_qty: Num
    accepted_inward: Num
    pending_to_procure: Num
    excess_inward: Num
    issued_qty: Num
    balance_to_issue: Num
    central_stock: Num
    procurement_pct: OptNum
    utilisation_pct: OptNum
    estimated_rate: OptNum
    actual_avg_rate: OptNum
    benchmark_rate: OptNum
    rate_variance_pct: OptNum
    estimated_value: OptNum
    procured_value: OptNum
    issued_value: OptNum
    stock_value: OptNum


class ProgressOut(Out):
    boq_id: uuid.UUID
    boq_no: str
    financial_year: str
    lines: list[ProgressLine]
    totals: dict[str, float | None]


class AllocationProgressLine(Out):
    material_id: uuid.UUID
    material_name: str
    uom: str
    project_id: uuid.UUID
    project_name: str
    sites: str | None
    allocated_qty: Num
    requested_qty: Num
    approved_qty: Num
    issued_qty: Num
    remaining_allocation: Num
    utilisation_pct: OptNum


# ------------------------------------------------------------------ helpers


async def _load(db, boq_id: uuid.UUID, *, lock: bool = False) -> Boq:
    return await get_or_404(db, Boq, boq_id, "BOQ", lock=lock, options=BOQ_FULL)


async def _summary(db, b: Boq, names: dict, item_count: int) -> BoqSummaryOut:
    return BoqSummaryOut(
        **{k: getattr(b, k) for k in BoqSummaryOut.model_fields if hasattr(b, k) and k not in ("item_count",)},
        item_count=item_count,
        created_by_name=names.get(b.created_by_id),
        approved_by_name=names.get(b.approved_by_id),
    )


async def _full(db, b: Boq) -> BoqOut:
    approvals = await approvals_for(db, "boq", b.id)
    names = await user_names(db, [b.created_by_id, b.approved_by_id, *[a.actor_id for a in approvals]])
    summary = await _summary(db, b, names, len(b.items))
    items = [
        BoqItemOut(
            id=it.id,
            line_no=it.line_no,
            material=mat_brief(it.material),
            planned_qty=it.planned_qty,
            estimated_rate=it.estimated_rate,
            estimated_amount=it.estimated_amount,
            benchmark_rate=it.benchmark_rate,
            remarks=it.remarks,
            allocations=[
                AllocationOut(
                    id=a.id,
                    project_id=a.project_id,
                    project_name=a.project.name,
                    site_id=a.site_id,
                    site_name=a.site.name if a.site else None,
                    allocated_qty=a.allocated_qty,
                )
                for a in it.allocations
            ],
        )
        for it in b.items
    ]
    return BoqOut(
        **summary.model_dump(),
        items=items,
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
    )


async def _build_items(db, body: BoqIn) -> list[BoqItem]:
    ensure_unique_materials(body.items)
    mats = await materials_by_id(db, [i.material_id for i in body.items])
    bench = await benchmarks(db, list(mats))
    project_ids = {a.project_id for i in body.items for a in i.allocations}
    site_ids = {a.site_id for i in body.items for a in i.allocations if a.site_id}
    projects = {p.id for p in (await db.execute(select(Project).where(Project.id.in_(project_ids)))).scalars()} if project_ids else set()
    sites = (
        {s.id: s.project_id for s in (await db.execute(select(Site).where(Site.id.in_(site_ids)))).unique().scalars()} if site_ids else {}
    )

    out = []
    for n, i in enumerate(body.items, start=1):
        m = mats[i.material_id]
        qty, rate = await to_base_uom(db, m, i.planned_qty, i.estimated_rate, i.uom_id)
        allocations, alloc_total = [], Decimal(0)
        for a in i.allocations:
            if a.project_id not in projects:
                raise AppError("INVALID_REFERENCE", "Allocation project not found", 422)
            if a.site_id and sites.get(a.site_id) != a.project_id:
                raise AppError("INVALID_REFERENCE", "Allocation site does not belong to the project", 422)
            a_qty, _ = await to_base_uom(db, m, a.allocated_qty, None, i.uom_id)
            alloc_total += a_qty
            allocations.append(BoqAllocation(id=uuid.uuid4(), project_id=a.project_id, site_id=a.site_id, allocated_qty=a_qty))
        if alloc_total > qty:
            raise AppError(
                "ALLOCATION_EXCEEDS_PLANNED",
                f"Allocations for {m.name} ({alloc_total}) exceed planned quantity ({qty})",
                422,
                {"material_id": str(m.id)},
            )
        out.append(
            BoqItem(
                id=uuid.uuid4(),
                line_no=n,
                material_id=m.id,
                planned_qty=qty,
                estimated_rate=rate,
                estimated_amount=q2(qty * rate),
                benchmark_rate=bench.get(m.id, {}).get("avg_rate"),
                remarks=i.remarks,
                allocations=allocations,
            )
        )
    return out


def _boq_snapshot(b: Boq) -> dict:
    snap = snapshot(b)
    snap["items"] = [
        {"material_id": str(i.material_id), "planned_qty": str(i.planned_qty), "estimated_rate": str(i.estimated_rate)} for i in b.items
    ]
    return snap


async def _respond(db, ctx: UserContext, boq_id: uuid.UUID, idem=None):
    return await finish(db, ctx, lambda: _load_full(db, boq_id), idem)


async def _load_full(db, boq_id: uuid.UUID) -> BoqOut:
    return await _full(db, await _load(db, boq_id))


# ------------------------------------------------------------------ endpoints


@router.get("", response_model=PageEnvelope[BoqSummaryOut])
async def list_boqs(
    db: DB,
    ctx: Perm("boq.read"),
    paging: PagingDep,
    status: BoqStatus | None = None,
    financial_year: str | None = None,
    q: str | None = None,
):
    stmt = select(Boq)
    if status:
        stmt = stmt.where(Boq.status == status)
    if financial_year:
        stmt = stmt.where(Boq.financial_year == financial_year)
    if q:
        stmt = stmt.where(or_(Boq.boq_no.ilike(f"%{q}%"), Boq.title.ilike(f"%{q}%")))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    boqs = (await db.execute(stmt.order_by(Boq.created_at.desc()).offset(paging.offset).limit(paging.limit))).scalars().all()
    counts = (
        dict(
            (
                await db.execute(
                    select(BoqItem.boq_id, func.count()).where(BoqItem.boq_id.in_([b.id for b in boqs])).group_by(BoqItem.boq_id)
                )
            ).all()
        )
        if boqs
        else {}
    )
    names = await user_names(db, [x for b in boqs for x in (b.created_by_id, b.approved_by_id)])
    return respond_page(ctx, [await _summary(db, b, names, counts.get(b.id, 0)) for b in boqs], total, paging)


@router.get("/current", response_model=Envelope[BoqOut | None], summary="The approved BOQ for a financial year (default: current)")
async def current_boq(db: DB, ctx: Perm("boq.read"), fy: str | None = None):
    fy = fy or financial_year()
    b = (
        await db.execute(select(Boq).where(Boq.financial_year == fy, Boq.status == BoqStatus.APPROVED).options(*BOQ_FULL))
    ).scalar_one_or_none()
    return respond(ctx, await _full(db, b) if b else None)


@router.post("", response_model=Envelope[BoqOut], status_code=201)
async def create_boq(body: BoqIn, db: DB, ctx: Perm("boq.create"), idem: IdemDep):
    if (cached := await idem.replay()) is not None:
        return cached
    fy = body.financial_year or financial_year()
    b = Boq(
        id=uuid.uuid4(),
        boq_no=await next_doc_no(db, DocType.BOQ),
        title=body.title,
        financial_year=fy,
        remarks=body.remarks,
        status=BoqStatus.DRAFT,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    b.root_boq_id = b.id
    b.items = await _build_items(db, body)
    b.total_estimated_value = q2(sum((i.estimated_amount for i in b.items), Decimal(0)))
    db.add(b)
    await db.flush()
    await audit(db, ctx, "CREATE", "boq", b.id, f"{b.boq_no} {b.title}", None, _boq_snapshot(b))
    return await _respond(db, ctx, b.id, idem)


@router.get("/{boq_id}", response_model=Envelope[BoqOut])
async def get_boq(boq_id: uuid.UUID, db: DB, ctx: Perm("boq.read")):
    return respond(ctx, await _full(db, await _load(db, boq_id)))


@router.put("/{boq_id}", response_model=Envelope[BoqOut], summary="Replace header + lines of a DRAFT/REJECTED BOQ")
async def update_boq(boq_id: uuid.UUID, body: BoqUpdateIn, db: DB, ctx: Perm("boq.create")):
    b = await _load(db, boq_id, lock=True)
    ensure_status(b, BoqStatus.DRAFT, BoqStatus.REJECTED, action="edit")
    check_version(b, body.version)
    before = _boq_snapshot(b)
    if body.financial_year and body.financial_year != b.financial_year and b.revision > 0:
        raise AppError("VALIDATION_ERROR", "A revision cannot change financial year", 422)
    b.title, b.remarks = body.title, body.remarks
    b.financial_year = body.financial_year or b.financial_year
    b.items.clear()
    await db.flush()
    b.items.extend(await _build_items(db, body))
    b.total_estimated_value = q2(sum((i.estimated_amount for i in b.items), Decimal(0)))
    b.status = BoqStatus.DRAFT
    bump(b, ctx)
    await db.flush()
    await audit(db, ctx, "UPDATE", "boq", b.id, b.boq_no, before, _boq_snapshot(b))
    return await _respond(db, ctx, b.id)


@router.post("/{boq_id}/submit", response_model=Envelope[BoqOut])
async def submit_boq(boq_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("boq.create")):
    b = await _load(db, boq_id, lock=True)
    ensure_status(b, BoqStatus.DRAFT, BoqStatus.REJECTED, action="submit")
    check_version(b, body.version)
    b.status, b.submitted_by_id, b.submitted_at = BoqStatus.SUBMITTED, ctx.user_id, now_utc()
    bump(b, ctx)
    await record_approval(db, ctx, "boq", b.id, "SUBMIT", ApprovalAction.SUBMITTED, None)
    await audit(db, ctx, "SUBMIT", "boq", b.id, b.boq_no)
    await notify(
        db,
        "boq.approve",
        type="BOQ_SUBMITTED",
        title=f"BOQ {b.boq_no} awaiting approval",
        body=b.title,
        entity_type="boq",
        entity_id=b.id,
        exclude=ctx.user_id,
    )
    return await _respond(db, ctx, b.id)


@router.post("/{boq_id}/approve", response_model=Envelope[BoqOut])
async def approve_boq(boq_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("boq.approve")):
    b = await _load(db, boq_id, lock=True)
    ensure_status(b, BoqStatus.SUBMITTED, action="approve")
    check_version(b, body.version)
    maker_checker(b.created_by_id, ctx)
    maker_checker(b.submitted_by_id, ctx)
    live = (
        await db.execute(select(Boq).where(Boq.financial_year == b.financial_year, Boq.status == BoqStatus.APPROVED).with_for_update())
    ).scalar_one_or_none()
    if live is not None and live.id != b.parent_boq_id:
        raise AppError(
            "BOQ_EXISTS_FOR_FY",
            f"{live.boq_no} is already approved for {b.financial_year}. Revise it instead of approving a second BOQ.",
            409,
            {"approved_boq_id": str(live.id)},
        )
    if live is not None:
        live.status = BoqStatus.SUPERSEDED
        bump(live, ctx)
        await db.flush()
        await audit(db, ctx, "SUPERSEDE", "boq", live.id, f"Superseded by {b.boq_no}")
    b.status, b.approved_by_id, b.approved_at = BoqStatus.APPROVED, ctx.user_id, now_utc()
    bump(b, ctx)
    await record_approval(db, ctx, "boq", b.id, "MANAGEMENT", ApprovalAction.APPROVED, body.comment)
    await audit(db, ctx, "APPROVE", "boq", b.id, b.boq_no)
    await notify(
        db,
        "boq.create",
        type="BOQ_APPROVED",
        title=f"BOQ {b.boq_no} approved",
        entity_type="boq",
        entity_id=b.id,
        exclude=ctx.user_id,
        also={b.created_by_id} if b.created_by_id else None,
    )
    return await _respond(db, ctx, b.id)


@router.post("/{boq_id}/reject", response_model=Envelope[BoqOut])
async def reject_boq(boq_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("boq.approve")):
    if not body.comment:
        raise AppError("VALIDATION_ERROR", "A comment is required when rejecting", 422)
    b = await _load(db, boq_id, lock=True)
    ensure_status(b, BoqStatus.SUBMITTED, action="reject")
    check_version(b, body.version)
    maker_checker(b.created_by_id, ctx, "reject")
    b.status = BoqStatus.REJECTED
    bump(b, ctx)
    await record_approval(db, ctx, "boq", b.id, "MANAGEMENT", ApprovalAction.REJECTED, body.comment)
    await audit(db, ctx, "REJECT", "boq", b.id, body.comment)
    notify_users(
        db, {b.created_by_id}, type="BOQ_REJECTED", title=f"BOQ {b.boq_no} rejected", body=body.comment, entity_type="boq", entity_id=b.id
    )
    return await _respond(db, ctx, b.id)


@router.post("/{boq_id}/revise", response_model=Envelope[BoqOut], status_code=201, summary="Start a revision (new DRAFT copy)")
async def revise_boq(boq_id: uuid.UUID, db: DB, ctx: Perm("boq.create")):
    b = await _load(db, boq_id, lock=True)
    ensure_status(b, BoqStatus.APPROVED, action="revise")
    pending = (
        await db.execute(
            select(Boq.boq_no).where(
                Boq.root_boq_id == (b.root_boq_id or b.id),
                Boq.status.in_([BoqStatus.DRAFT, BoqStatus.SUBMITTED, BoqStatus.REJECTED]),
            )
        )
    ).scalar_one_or_none()
    if pending:
        raise AppError("REVISION_IN_PROGRESS", f"Revision {pending} is already in progress", 409)
    rev_no = b.revision + 1
    root_no = b.boq_no.split("-R")[0]
    new = Boq(
        id=uuid.uuid4(),
        boq_no=f"{root_no}-R{rev_no}",
        title=b.title,
        financial_year=b.financial_year,
        revision=rev_no,
        root_boq_id=b.root_boq_id or b.id,
        parent_boq_id=b.id,
        status=BoqStatus.DRAFT,
        remarks=b.remarks,
        total_estimated_value=b.total_estimated_value,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
        items=[
            BoqItem(
                id=uuid.uuid4(),
                line_no=i.line_no,
                material_id=i.material_id,
                planned_qty=i.planned_qty,
                estimated_rate=i.estimated_rate,
                estimated_amount=i.estimated_amount,
                benchmark_rate=i.benchmark_rate,
                remarks=i.remarks,
                allocations=[
                    BoqAllocation(id=uuid.uuid4(), project_id=a.project_id, site_id=a.site_id, allocated_qty=a.allocated_qty)
                    for a in i.allocations
                ],
            )
            for i in b.items
        ],
    )
    db.add(new)
    await db.flush()
    await audit(db, ctx, "REVISE", "boq", new.id, f"Revision {rev_no} of {b.boq_no}")
    return await _respond(db, ctx, new.id)


@router.post("/{boq_id}/close", response_model=Envelope[BoqOut])
async def close_boq(boq_id: uuid.UUID, body: CommentIn, db: DB, ctx: Perm("boq.approve")):
    b = await _load(db, boq_id, lock=True)
    ensure_status(b, BoqStatus.APPROVED, action="close")
    check_version(b, body.version)
    b.status = BoqStatus.CLOSED
    bump(b, ctx)
    await record_approval(db, ctx, "boq", b.id, "MANAGEMENT", ApprovalAction.CLOSED, body.comment)
    await audit(db, ctx, "CLOSE", "boq", b.id, b.boq_no)
    return await _respond(db, ctx, b.id)


@router.post("/{boq_id}/cancel", response_model=Envelope[BoqOut], summary="Discard a draft / rejected BOQ")
async def cancel_boq(boq_id: uuid.UUID, body: VersionIn, db: DB, ctx: Perm("boq.create")):
    b = await _load(db, boq_id, lock=True)
    ensure_status(b, BoqStatus.DRAFT, BoqStatus.REJECTED, action="cancel")
    check_version(b, body.version)
    b.status = BoqStatus.CLOSED
    bump(b, ctx)
    await audit(db, ctx, "CANCEL", "boq", b.id, b.boq_no)
    return await _respond(db, ctx, b.id)


@router.get("/{boq_id}/progress", response_model=Envelope[ProgressOut], summary="Planned / inward / pending / issued / stock per line")
async def progress(boq_id: uuid.UUID, db: DB, ctx: Perm("boq.read")):
    b = await _load(db, boq_id)
    lines = await boq_progress(db, b)
    totals = {k: float(sum((ln[k] or 0) for ln in lines)) for k in ("estimated_value", "procured_value", "issued_value", "stock_value")}
    return respond(
        ctx,
        ProgressOut(
            boq_id=b.id, boq_no=b.boq_no, financial_year=b.financial_year, lines=[ProgressLine(**ln) for ln in lines], totals=totals
        ),
    )


@router.get("/{boq_id}/allocation-progress", response_model=Envelope[list[AllocationProgressLine]])
async def alloc_progress(boq_id: uuid.UUID, db: DB, ctx: Perm("boq.read")):
    b = await _load(db, boq_id)
    return respond(ctx, [AllocationProgressLine(**r) for r in await allocation_progress(db, b)])
