"""BOQ figures shared by the BOQ screen, reports, dashboards and RFQ allocation checks.

pending_to_procure = planned − accepted_inward        (1,00,000 − 10,000 = 90,000)
balance_to_issue   = planned − issued
procurement %      = accepted_inward / planned
utilisation %      = issued / planned
rate variance %    = (actual avg GRN rate − BOQ estimated rate) / estimated rate
"""

import uuid
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Boq,
    BoqAllocation,
    BoqItem,
    Grn,
    GrnItem,
    IssueNote,
    IssueNoteItem,
    MaterialRequest,
    MaterialRequestItem,
    StockBalance,
    Store,
)
from app.models.enums import BoqStatus, GrnStatus, IssueStatus, RfqStatus, StoreType
from app.services.common import fy_bounds, q2, q4

ZERO = Decimal("0")
ISSUED_STATES = (IssueStatus.ISSUED, IssueStatus.RECEIVED, IssueStatus.PARTIALLY_RECEIVED)
LIVE_RFQ_STATES = (
    RfqStatus.SUBMITTED,
    RfqStatus.PM_APPROVED,
    RfqStatus.STORE_APPROVED,
    RfqStatus.PARTIALLY_ISSUED,
    RfqStatus.ISSUED,
    RfqStatus.CLOSED,
)

BOQ_FULL = (selectinload(Boq.items).selectinload(BoqItem.allocations),)


def pct(num: Decimal, den: Decimal) -> Decimal | None:
    return q2(num / den * 100) if den else None


async def approved_boq_for_fy(db: AsyncSession, fy: str) -> Boq | None:
    return (
        await db.execute(select(Boq).where(Boq.financial_year == fy, Boq.status == BoqStatus.APPROVED).options(*BOQ_FULL))
    ).scalar_one_or_none()


def lineage_ids(boq: Boq):
    root = boq.root_boq_id or boq.id
    return select(Boq.id).where(or_(Boq.id == root, Boq.root_boq_id == root))


async def inward_by_material(db: AsyncSession, boq: Boq) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    rows = await db.execute(
        select(GrnItem.material_id, func.sum(GrnItem.accepted_qty), func.sum(GrnItem.amount))
        .join(Grn, Grn.id == GrnItem.grn_id)
        .where(Grn.status == GrnStatus.POSTED, Grn.boq_id.in_(lineage_ids(boq)))
        .group_by(GrnItem.material_id)
    )
    return {m: (Decimal(q or 0), Decimal(v or 0)) for m, q, v in rows}


async def issued_by_material(db: AsyncSession, fy: str, project_id: uuid.UUID | None = None) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    start, end = fy_bounds(fy)
    stmt = (
        select(IssueNoteItem.material_id, func.sum(IssueNoteItem.issued_qty), func.sum(IssueNoteItem.amount))
        .join(IssueNote, IssueNote.id == IssueNoteItem.issue_note_id)
        .where(IssueNote.status.in_(ISSUED_STATES), IssueNote.issue_date.between(start, end))
        .group_by(IssueNoteItem.material_id)
    )
    if project_id:
        stmt = stmt.where(IssueNote.project_id == project_id)
    return {m: (Decimal(q or 0), Decimal(v or 0)) for m, q, v in await db.execute(stmt)}


async def central_stock_by_material(db: AsyncSession) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    rows = await db.execute(
        select(StockBalance.material_id, func.sum(StockBalance.qty), func.sum(StockBalance.value))
        .join(Store, Store.id == StockBalance.store_id)
        .where(Store.type == StoreType.CENTRAL)
        .group_by(StockBalance.material_id)
    )
    return {m: (Decimal(q or 0), Decimal(v or 0)) for m, q, v in rows}


async def boq_progress(db: AsyncSession, boq: Boq) -> list[dict]:
    inward = await inward_by_material(db, boq)
    issued = await issued_by_material(db, boq.financial_year)
    stock = await central_stock_by_material(db)
    lines = []
    for it in boq.items:
        planned = Decimal(it.planned_qty)
        in_qty, in_val = inward.get(it.material_id, (ZERO, ZERO))
        out_qty, out_val = issued.get(it.material_id, (ZERO, ZERO))
        st_qty, st_val = stock.get(it.material_id, (ZERO, ZERO))
        actual_avg = q4(in_val / in_qty) if in_qty else None
        lines.append(
            {
                "material_id": it.material_id,
                "material_code": it.material.code,
                "material_name": it.material.name,
                "uom": it.material.base_uom.code,
                "planned_qty": planned,
                "accepted_inward": in_qty,
                "pending_to_procure": max(planned - in_qty, ZERO),
                "excess_inward": max(in_qty - planned, ZERO),
                "issued_qty": out_qty,
                "balance_to_issue": max(planned - out_qty, ZERO),
                "central_stock": st_qty,
                "procurement_pct": pct(in_qty, planned),
                "utilisation_pct": pct(out_qty, planned),
                "estimated_rate": it.estimated_rate,
                "actual_avg_rate": actual_avg,
                "benchmark_rate": it.benchmark_rate,
                "rate_variance_pct": pct(actual_avg - it.estimated_rate, it.estimated_rate) if actual_avg is not None else None,
                "estimated_value": it.estimated_amount,
                "procured_value": q2(in_val),
                "issued_value": q2(out_val),
                "stock_value": q2(st_val),
            }
        )
    return lines


async def project_allocation_usage(
    db: AsyncSession, fy: str, project_id: uuid.UUID, material_ids: list[uuid.UUID], exclude_request_id: uuid.UUID | None = None
) -> dict[uuid.UUID, dict[str, Decimal]]:
    """Allocated (from the approved BOQ) vs approved-in-RFQs vs issued, per material, for one project."""
    start, end = fy_bounds(fy)
    out = {m: {"allocated": ZERO, "approved": ZERO, "requested": ZERO, "issued": ZERO, "has_allocation": False} for m in material_ids}

    boq = await approved_boq_for_fy(db, fy)
    if boq is not None:
        for it in boq.items:
            if it.material_id in out:
                for a in it.allocations:
                    if a.project_id == project_id:
                        out[it.material_id]["allocated"] += Decimal(a.allocated_qty)
                        out[it.material_id]["has_allocation"] = True

    stmt = (
        select(
            MaterialRequestItem.material_id,
            func.sum(MaterialRequestItem.requested_qty),
            func.sum(func.coalesce(MaterialRequestItem.approved_qty, 0)),
        )
        .join(MaterialRequest, MaterialRequest.id == MaterialRequestItem.request_id)
        .where(
            MaterialRequest.project_id == project_id,
            MaterialRequest.status.in_(LIVE_RFQ_STATES),
            MaterialRequest.request_date.between(start, end),
            MaterialRequestItem.material_id.in_(material_ids),
        )
        .group_by(MaterialRequestItem.material_id)
    )
    if exclude_request_id:
        stmt = stmt.where(MaterialRequest.id != exclude_request_id)
    for m, req, appr in await db.execute(stmt):
        out[m]["requested"] = Decimal(req or 0)
        out[m]["approved"] = Decimal(appr or 0)

    for m, (q, _) in (await issued_by_material(db, fy, project_id)).items():
        if m in out:
            out[m]["issued"] = q
    return out


async def allocation_progress(db: AsyncSession, boq: Boq) -> list[dict]:
    """Per project (and site, if allocated at site level): allocated vs requested vs issued."""
    start, end = fy_bounds(boq.financial_year)
    req_rows = await db.execute(
        select(
            MaterialRequest.project_id,
            MaterialRequestItem.material_id,
            func.sum(MaterialRequestItem.requested_qty),
            func.sum(func.coalesce(MaterialRequestItem.approved_qty, 0)),
        )
        .join(MaterialRequest, MaterialRequest.id == MaterialRequestItem.request_id)
        .where(MaterialRequest.status.in_(LIVE_RFQ_STATES), MaterialRequest.request_date.between(start, end))
        .group_by(MaterialRequest.project_id, MaterialRequestItem.material_id)
    )
    requested = {(p, m): (Decimal(r or 0), Decimal(a or 0)) for p, m, r, a in req_rows}
    iss_rows = await db.execute(
        select(IssueNote.project_id, IssueNoteItem.material_id, func.sum(IssueNoteItem.issued_qty))
        .join(IssueNote, IssueNote.id == IssueNoteItem.issue_note_id)
        .where(IssueNote.status.in_(ISSUED_STATES), IssueNote.issue_date.between(start, end))
        .group_by(IssueNote.project_id, IssueNoteItem.material_id)
    )
    issued = {(p, m): Decimal(q or 0) for p, m, q in iss_rows}

    out = []
    for it in boq.items:
        per_project: dict[uuid.UUID, dict] = {}
        for a in it.allocations:
            row = per_project.setdefault(
                a.project_id,
                {"project_id": a.project_id, "project_name": a.project.name, "allocated_qty": ZERO, "sites": []},
            )
            row["allocated_qty"] += Decimal(a.allocated_qty)
            if a.site_id:
                row["sites"].append(a.site.name)
        for pid, row in per_project.items():
            req, appr = requested.get((pid, it.material_id), (ZERO, ZERO))
            iss = issued.get((pid, it.material_id), ZERO)
            out.append(
                {
                    "material_id": it.material_id,
                    "material_name": it.material.name,
                    "uom": it.material.base_uom.code,
                    "project_id": pid,
                    "project_name": row["project_name"],
                    "sites": ", ".join(row["sites"]) or None,
                    "allocated_qty": row["allocated_qty"],
                    "requested_qty": req,
                    "approved_qty": appr,
                    "issued_qty": iss,
                    "remaining_allocation": row["allocated_qty"] - appr,
                    "utilisation_pct": pct(iss, row["allocated_qty"]),
                }
            )
    return out


__all__ = [
    "BOQ_FULL",
    "BoqAllocation",
    "allocation_progress",
    "and_",
    "approved_boq_for_fy",
    "boq_progress",
    "central_stock_by_material",
    "inward_by_material",
    "issued_by_material",
    "project_allocation_usage",
]
