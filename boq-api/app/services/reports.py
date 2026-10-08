"""Report registry. Every report declares its level, filters and columns, so one generic React screen
(and one Flutter screen later) can render, filter and export all of them.

Scoping: rows are always restricted to what the caller's `reports.read` scope covers (project/site/store);
columns flagged `finance` are dropped for users without finance.view.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    Approval,
    Boq,
    ConsumptionEntry,
    Grn,
    GrnItem,
    IssueNote,
    IssueNoteItem,
    Material,
    MaterialCategory,
    MaterialRequest,
    MaterialRequestItem,
    Project,
    Site,
    StockAdjustment,
    StockAdjustmentItem,
    StockBalance,
    StockLedger,
    Store,
    Uom,
    Vendor,
)
from app.models.enums import (
    AdjustmentStatus,
    BoqStatus,
    EntryStatus,
    GrnStatus,
    IssueStatus,
    RfqStatus,
    StoreType,
)
from app.services.boq import BOQ_FULL, allocation_progress, boq_progress
from app.services.common import financial_year, fy_bounds, q2, today
from app.services.context import ScopeSet, UserContext

ZERO = Decimal("0")


@dataclass
class Col:
    key: str
    label: str
    type: str = "text"  # text | qty | money | rate | pct | date | datetime | int
    finance: bool = False


@dataclass
class Params:
    fy: str
    date_from: date
    date_to: date
    project_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    material_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    days: int = 90


@dataclass
class Report:
    key: str
    title: str
    level: str  # management | store | project | site | market | audit
    description: str
    columns: list[Col]
    fn: Callable[[AsyncSession, UserContext, Params, ScopeSet], Awaitable[list[dict]]]
    filters: list[str] = field(default_factory=list)
    requires: tuple[str, ...] = ()  # extra permissions beyond reports.read
    required_filters: tuple[str, ...] = ()


REGISTRY: dict[str, Report] = {}


def report(**kw):
    def deco(fn):
        REGISTRY[kw["key"]] = Report(fn=fn, **kw)
        return fn

    return deco


# ------------------------------------------------------------------ scope helpers


def _site_scope(scope: ScopeSet, site_col, project_col=None):
    if scope.all:
        return true()
    conds = []
    if scope.site_ids:
        conds.append(site_col.in_(scope.site_ids))
    if project_col is not None and scope.project_ids:
        conds.append(project_col.in_(scope.project_ids))
    from sqlalchemy import false, or_

    return or_(*conds) if conds else false()


def _store_scope(scope: ScopeSet, store_col, site_col=None):
    if scope.all:
        return true()
    from sqlalchemy import false, or_

    conds = []
    if scope.store_ids:
        conds.append(store_col.in_(scope.store_ids))
    if site_col is not None and scope.site_ids:
        conds.append(site_col.in_(scope.site_ids))
    return or_(*conds) if conds else false()


def _mat_cols():
    return [Material.code.label("material_code"), Material.name.label("material_name"), Uom.code.label("uom")]


async def _approved_or_latest_boq(db: AsyncSession, fy: str) -> Boq | None:
    stmt = (
        select(Boq)
        .where(Boq.financial_year == fy, Boq.status.in_([BoqStatus.APPROVED, BoqStatus.CLOSED]))
        .order_by((Boq.status == BoqStatus.APPROVED).desc(), Boq.revision.desc())
        .options(*BOQ_FULL)
    )
    return (await db.execute(stmt)).scalars().first()


# ------------------------------------------------------------------ management


@report(
    key="boq-vs-actual",
    title="BOQ vs Actual",
    level="management",
    description="Per material: planned, inward (accepted), pending to procure, issued, central stock.",
    filters=["fy", "category_id"],
    requires=("boq.read",),
    columns=[
        Col("material_code", "Code"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("planned_qty", "Planned", "qty"),
        Col("accepted_inward", "Inward", "qty"),
        Col("pending_to_procure", "Pending to procure", "qty"),
        Col("issued_qty", "Issued", "qty"),
        Col("balance_to_issue", "Balance to issue", "qty"),
        Col("central_stock", "Central stock", "qty"),
        Col("procurement_pct", "Procured %", "pct"),
        Col("utilisation_pct", "Utilised %", "pct"),
        Col("estimated_value", "Est. value", "money", True),
        Col("procured_value", "Procured value", "money", True),
    ],
)
async def _boq_vs_actual(db, ctx, p, scope):
    boq = await _approved_or_latest_boq(db, p.fy)
    if boq is None:
        return []
    lines = await boq_progress(db, boq)
    if p.category_id:
        cat_mats = set((await db.execute(select(Material.id).where(Material.category_id == p.category_id))).scalars())
        lines = [ln for ln in lines if ln["material_id"] in cat_mats]
    return lines


@report(
    key="budget-vs-actual",
    title="Budget vs Actual (value)",
    level="management",
    description="BOQ estimated value vs procured, issued and in-stock value per material.",
    filters=["fy"],
    requires=("boq.read", "finance.view"),
    columns=[
        Col("material_name", "Material"),
        Col("estimated_value", "Budget (BOQ)", "money", True),
        Col("procured_value", "Procured", "money", True),
        Col("issued_value", "Issued", "money", True),
        Col("stock_value", "In stock", "money", True),
        Col("budget_used_pct", "Budget used %", "pct", True),
    ],
)
async def _budget_vs_actual(db, ctx, p, scope):
    boq = await _approved_or_latest_boq(db, p.fy)
    if boq is None:
        return []
    out = []
    for ln in await boq_progress(db, boq):
        est = Decimal(ln["estimated_value"] or 0)
        out.append({**ln, "budget_used_pct": q2(ln["procured_value"] / est * 100) if est else None})
    return out


@report(
    key="rate-variance",
    title="Rate variance",
    level="management",
    description="BOQ estimated rate vs market benchmark vs actual weighted-average purchase rate.",
    filters=["fy"],
    requires=("boq.read", "finance.view"),
    columns=[
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("estimated_rate", "BOQ rate", "rate", True),
        Col("benchmark_rate", "Market benchmark", "rate", True),
        Col("actual_avg_rate", "Actual avg rate", "rate", True),
        Col("rate_variance_pct", "Variance vs BOQ %", "pct", True),
        Col("benchmark_gap_pct", "Gap vs market %", "pct", True),
    ],
)
async def _rate_variance(db, ctx, p, scope):
    boq = await _approved_or_latest_boq(db, p.fy)
    if boq is None:
        return []
    out = []
    for ln in await boq_progress(db, boq):
        bench, actual = ln["benchmark_rate"], ln["actual_avg_rate"]
        gap = q2((actual - bench) / bench * 100) if (bench and actual is not None) else None
        out.append({**ln, "benchmark_gap_pct": gap})
    return out


@report(
    key="project-material-cost",
    title="Project-wise material issued & consumed",
    level="project",
    description="Material issued to and consumed at each project/site in the period.",
    filters=["date_from", "date_to", "project_id", "site_id", "material_id"],
    columns=[
        Col("project_name", "Project"),
        Col("site_name", "Site"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("issued_qty", "Issued", "qty"),
        Col("received_qty", "Received", "qty"),
        Col("consumed_qty", "Consumed", "qty"),
        Col("issued_value", "Issued value", "money", True),
        Col("consumed_value", "Consumed value", "money", True),
    ],
)
async def _project_material_cost(db, ctx, p, scope):
    iss = (
        select(
            IssueNote.site_id.label("site_id"),
            IssueNoteItem.material_id.label("material_id"),
            func.sum(IssueNoteItem.issued_qty).label("issued_qty"),
            func.sum(func.coalesce(IssueNoteItem.received_qty, 0)).label("received_qty"),
            func.sum(IssueNoteItem.amount).label("issued_value"),
        )
        .join(IssueNote, IssueNote.id == IssueNoteItem.issue_note_id)
        .where(
            IssueNote.status.in_([IssueStatus.ISSUED, IssueStatus.RECEIVED, IssueStatus.PARTIALLY_RECEIVED]),
            IssueNote.issue_date.between(p.date_from, p.date_to),
        )
        .group_by(IssueNote.site_id, IssueNoteItem.material_id)
    )
    cons = (
        select(
            ConsumptionEntry.site_id.label("site_id"),
            ConsumptionEntry.material_id.label("material_id"),
            func.sum(ConsumptionEntry.qty).label("consumed_qty"),
            func.sum(ConsumptionEntry.amount).label("consumed_value"),
        )
        .where(ConsumptionEntry.status == EntryStatus.POSTED, ConsumptionEntry.entry_date.between(p.date_from, p.date_to))
        .group_by(ConsumptionEntry.site_id, ConsumptionEntry.material_id)
    )
    rows: dict[tuple, dict] = {}
    for r in (await db.execute(iss)).mappings():
        rows[(r["site_id"], r["material_id"])] = dict(r)
    for r in (await db.execute(cons)).mappings():
        rows.setdefault((r["site_id"], r["material_id"]), {"site_id": r["site_id"], "material_id": r["material_id"]}).update(dict(r))
    if not rows:
        return []
    site_ids = {k[0] for k in rows}
    sites = {
        s.id: s
        for s in (await db.execute(select(Site).where(Site.id.in_(site_ids), _site_scope(scope, Site.id, Site.project_id))))
        .unique()
        .scalars()
    }
    mats = {m.id: m for m in (await db.execute(select(Material).where(Material.id.in_({k[1] for k in rows})))).unique().scalars()}
    out = []
    for (sid, mid), r in rows.items():
        s = sites.get(sid)
        if (
            s is None
            or (p.project_id and s.project_id != p.project_id)
            or (p.site_id and sid != p.site_id)
            or (p.material_id and mid != p.material_id)
        ):
            continue
        m = mats[mid]
        out.append(
            {
                "project_name": s.project.name,
                "site_name": s.name,
                "material_name": m.name,
                "uom": m.base_uom.code,
                "issued_qty": r.get("issued_qty") or ZERO,
                "received_qty": r.get("received_qty") or ZERO,
                "consumed_qty": r.get("consumed_qty") or ZERO,
                "issued_value": q2(r.get("issued_value") or 0),
                "consumed_value": q2(r.get("consumed_value") or 0),
            }
        )
    return sorted(out, key=lambda x: (x["project_name"], x["site_name"], x["material_name"]))


@report(
    key="rfq-turnaround",
    title="RFQ turnaround",
    level="management",
    description="Hours from submission to PM approval, store approval and first issue.",
    filters=["date_from", "date_to", "project_id", "site_id"],
    requires=("rfq.read",),
    columns=[
        Col("request_no", "Request"),
        Col("site_name", "Site"),
        Col("priority", "Priority"),
        Col("status", "Status"),
        Col("submitted_at", "Submitted", "datetime"),
        Col("hours_to_pm", "Hrs → PM approval", "qty"),
        Col("hours_to_store", "Hrs → store approval", "qty"),
        Col("hours_to_issue", "Hrs → first issue", "qty"),
    ],
)
async def _rfq_turnaround(db, ctx, p, scope):
    M = MaterialRequest
    pm = (
        select(Approval.entity_id, func.min(Approval.acted_at).label("at"))
        .where(Approval.entity_type == "rfq", Approval.stage == "PROJECT_MANAGER", Approval.action == "APPROVED")
        .group_by(Approval.entity_id)
        .subquery()
    )
    st = (
        select(Approval.entity_id, func.min(Approval.acted_at).label("at"))
        .where(Approval.entity_type == "rfq", Approval.stage == "STORE", Approval.action == "APPROVED")
        .group_by(Approval.entity_id)
        .subquery()
    )
    stmt = (
        select(
            M.request_no,
            Site.name.label("site_name"),
            M.priority,
            M.status,
            M.submitted_at,
            pm.c.at.label("pm_at"),
            st.c.at.label("st_at"),
            M.first_issued_at,
        )
        .join(Site, Site.id == M.site_id)
        .outerjoin(pm, pm.c.entity_id == M.id)
        .outerjoin(st, st.c.entity_id == M.id)
        .where(M.submitted_at.is_not(None), M.request_date.between(p.date_from, p.date_to), _site_scope(scope, M.site_id, M.project_id))
        .order_by(M.submitted_at.desc())
    )
    if p.project_id:
        stmt = stmt.where(M.project_id == p.project_id)
    if p.site_id:
        stmt = stmt.where(M.site_id == p.site_id)

    def hrs(a, b):
        return q2(Decimal((b - a).total_seconds()) / 3600) if a and b else None

    return [
        {
            "request_no": r.request_no,
            "site_name": r.site_name,
            "priority": r.priority,
            "status": r.status,
            "submitted_at": r.submitted_at,
            "hours_to_pm": hrs(r.submitted_at, r.pm_at),
            "hours_to_store": hrs(r.submitted_at, r.st_at),
            "hours_to_issue": hrs(r.submitted_at, r.first_issued_at),
        }
        for r in await db.execute(stmt)
    ]


# ------------------------------------------------------------------ central store


@report(
    key="stock-summary",
    title="Stock summary",
    level="store",
    description="Current quantity, weighted average rate and value per store and material.",
    filters=["store_id", "category_id"],
    requires=("stock.read",),
    columns=[
        Col("store_name", "Store"),
        Col("category", "Category"),
        Col("material_code", "Code"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("qty", "Qty", "qty"),
        Col("reorder_level", "Reorder level", "qty"),
        Col("stock_status", "Status"),
        Col("avg_rate", "Avg rate", "rate", True),
        Col("value", "Value", "money", True),
    ],
)
async def _stock_summary(db, ctx, p, scope):
    stmt = (
        select(
            Store.name.label("store_name"),
            Store.type.label("store_type"),
            MaterialCategory.name.label("category"),
            *_mat_cols(),
            StockBalance.qty,
            Material.reorder_level,
            StockBalance.avg_rate,
            StockBalance.value,
        )
        .join(Store, Store.id == StockBalance.store_id)
        .join(Material, Material.id == StockBalance.material_id)
        .join(MaterialCategory, MaterialCategory.id == Material.category_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .where(StockBalance.qty > 0, _store_scope(scope, StockBalance.store_id, Store.site_id))
        .order_by(Store.name, MaterialCategory.name, Material.name)
    )
    if p.store_id:
        stmt = stmt.where(StockBalance.store_id == p.store_id)
    if p.category_id:
        stmt = stmt.where(Material.category_id == p.category_id)
    out = []
    for r in (await db.execute(stmt)).mappings():
        low = r["store_type"] == StoreType.CENTRAL and r["reorder_level"] > 0 and r["qty"] < r["reorder_level"]
        out.append({**r, "value": q2(r["value"]), "stock_status": "Below reorder" if low else "OK"})
    return out


@report(
    key="bin-card",
    title="Stock ledger (bin card)",
    level="store",
    description="Opening balance, every movement and closing balance for one material in one store.",
    filters=["store_id", "material_id", "date_from", "date_to"],
    required_filters=("store_id", "material_id"),
    requires=("stock.read",),
    columns=[
        Col("txn_date", "Date", "date"),
        Col("txn_type", "Type"),
        Col("ref_no", "Document"),
        Col("qty_in", "In", "qty"),
        Col("qty_out", "Out", "qty"),
        Col("balance_qty", "Balance", "qty"),
        Col("rate", "Rate", "rate", True),
        Col("value", "Value", "money", True),
        Col("balance_value", "Balance value", "money", True),
    ],
)
async def _bin_card(db, ctx, p, scope):
    store = await db.get(Store, p.store_id)
    if store is None or not scope.covers(site_id=store.site_id, store_ids=(store.id,)):
        return []
    L = StockLedger
    base = and_(L.store_id == p.store_id, L.material_id == p.material_id)
    opening = (
        await db.execute(
            select(L.balance_qty, L.balance_value).where(base, L.txn_date < p.date_from).order_by(L.txn_date.desc(), L.id.desc()).limit(1)
        )
    ).first()
    rows = (await db.execute(select(L).where(base, L.txn_date.between(p.date_from, p.date_to)).order_by(L.txn_date, L.id))).scalars().all()
    out = [
        {
            "txn_date": p.date_from,
            "txn_type": "OPENING",
            "ref_no": None,
            "qty_in": None,
            "qty_out": None,
            "balance_qty": opening[0] if opening else ZERO,
            "rate": None,
            "value": None,
            "balance_value": q2(opening[1]) if opening else ZERO,
        }
    ]
    for r in rows:
        out.append(
            {
                "txn_date": r.txn_date,
                "txn_type": r.txn_type,
                "ref_no": r.ref_no,
                "qty_in": r.qty_in or None,
                "qty_out": r.qty_out or None,
                "balance_qty": r.balance_qty,
                "rate": r.rate,
                "value": q2(r.value),
                "balance_value": q2(r.balance_value),
            }
        )
    last = out[-1]
    out.append(
        {
            **{c: None for c in last},
            "txn_date": p.date_to,
            "txn_type": "CLOSING",
            "balance_qty": last["balance_qty"],
            "balance_value": last["balance_value"],
        }
    )
    return out


@report(
    key="grn-register",
    title="Inward register (GRN)",
    level="store",
    description="Every posted goods receipt line in the period.",
    filters=["date_from", "date_to", "vendor_id", "material_id", "store_id"],
    requires=("grn.read",),
    columns=[
        Col("grn_no", "GRN"),
        Col("received_date", "Date", "date"),
        Col("vendor_name", "Vendor"),
        Col("invoice_no", "Invoice"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("received_qty", "Received", "qty"),
        Col("rejected_qty", "Rejected", "qty"),
        Col("accepted_qty", "Accepted", "qty"),
        Col("rate", "Rate", "rate", True),
        Col("amount", "Amount", "money", True),
        Col("gst_amount", "GST", "money", True),
        Col("status", "Status"),
    ],
)
async def _grn_register(db, ctx, p, scope):
    stmt = (
        select(
            Grn.grn_no,
            Grn.received_date,
            Vendor.name.label("vendor_name"),
            Grn.invoice_no,
            Grn.status,
            *_mat_cols(),
            GrnItem.received_qty,
            GrnItem.rejected_qty,
            GrnItem.accepted_qty,
            GrnItem.rate,
            GrnItem.amount,
            GrnItem.gst_amount,
        )
        .join(Grn, Grn.id == GrnItem.grn_id)
        .join(Vendor, Vendor.id == Grn.vendor_id)
        .join(Material, Material.id == GrnItem.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .where(
            Grn.status.in_([GrnStatus.POSTED, GrnStatus.CANCELLED]),
            Grn.received_date.between(p.date_from, p.date_to),
            _store_scope(scope, Grn.store_id),
        )
        .order_by(Grn.received_date, Grn.grn_no, GrnItem.line_no)
    )
    for col, val in ((Grn.vendor_id, p.vendor_id), (GrnItem.material_id, p.material_id), (Grn.store_id, p.store_id)):
        if val:
            stmt = stmt.where(col == val)
    return [dict(r) for r in (await db.execute(stmt)).mappings()]


@report(
    key="vendor-inward",
    title="Vendor-wise inward",
    level="store",
    description="Receipts per vendor in the period.",
    filters=["date_from", "date_to"],
    requires=("grn.read",),
    columns=[
        Col("vendor_name", "Vendor"),
        Col("grn_count", "GRNs", "int"),
        Col("rejected_lines", "Lines with rejection", "int"),
        Col("total_amount", "Amount", "money", True),
        Col("total_gst", "GST", "money", True),
    ],
)
async def _vendor_inward(db, ctx, p, scope):
    rej = select(GrnItem.grn_id, func.count().label("n")).where(GrnItem.rejected_qty > 0).group_by(GrnItem.grn_id).subquery()
    stmt = (
        select(
            Vendor.name.label("vendor_name"),
            func.count(Grn.id).label("grn_count"),
            func.coalesce(func.sum(rej.c.n), 0).label("rejected_lines"),
            func.sum(Grn.total_amount).label("total_amount"),
            func.sum(Grn.total_gst).label("total_gst"),
        )
        .join(Vendor, Vendor.id == Grn.vendor_id)
        .outerjoin(rej, rej.c.grn_id == Grn.id)
        .where(Grn.status == GrnStatus.POSTED, Grn.received_date.between(p.date_from, p.date_to), _store_scope(scope, Grn.store_id))
        .group_by(Vendor.name)
        .order_by(func.sum(Grn.total_amount).desc())
    )
    return [dict(r) for r in (await db.execute(stmt)).mappings()]


@report(
    key="issue-register",
    title="Issue register",
    level="store",
    description="Every issue note line in the period with receipt status.",
    filters=["date_from", "date_to", "project_id", "site_id", "material_id"],
    requires=("issue.read",),
    columns=[
        Col("issue_no", "Issue note"),
        Col("issue_date", "Date", "date"),
        Col("request_no", "Request"),
        Col("project_name", "Project"),
        Col("site_name", "Site"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("issued_qty", "Issued", "qty"),
        Col("received_qty", "Received", "qty"),
        Col("shortage_qty", "Short", "qty"),
        Col("damaged_qty", "Damaged", "qty"),
        Col("rate", "Rate", "rate", True),
        Col("amount", "Amount", "money", True),
        Col("status", "Status"),
    ],
)
async def _issue_register(db, ctx, p, scope):
    stmt = (
        select(
            IssueNote.issue_no,
            IssueNote.issue_date,
            MaterialRequest.request_no,
            Project.name.label("project_name"),
            Site.name.label("site_name"),
            *_mat_cols(),
            IssueNoteItem.issued_qty,
            IssueNoteItem.received_qty,
            IssueNoteItem.shortage_qty,
            IssueNoteItem.damaged_qty,
            IssueNoteItem.rate,
            IssueNoteItem.amount,
            IssueNote.status,
        )
        .join(IssueNote, IssueNote.id == IssueNoteItem.issue_note_id)
        .join(MaterialRequest, MaterialRequest.id == IssueNote.request_id)
        .join(Project, Project.id == IssueNote.project_id)
        .join(Site, Site.id == IssueNote.site_id)
        .join(Material, Material.id == IssueNoteItem.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .where(
            IssueNote.status != IssueStatus.DRAFT,
            IssueNote.issue_date.between(p.date_from, p.date_to),
            _site_scope(scope, IssueNote.site_id, IssueNote.project_id)
            if not scope.store_ids
            else _store_scope(scope, IssueNote.from_store_id, IssueNote.site_id),
        )
        .order_by(IssueNote.issue_date, IssueNote.issue_no, IssueNoteItem.line_no)
    )
    for col, val in ((IssueNote.project_id, p.project_id), (IssueNote.site_id, p.site_id), (IssueNoteItem.material_id, p.material_id)):
        if val:
            stmt = stmt.where(col == val)
    return [dict(r) for r in (await db.execute(stmt)).mappings()]


@report(
    key="pending-rfqs",
    title="Pending requests (to issue)",
    level="store",
    description="Approved request lines not yet fully issued, with ageing.",
    filters=["project_id", "site_id"],
    requires=("rfq.read",),
    columns=[
        Col("request_no", "Request"),
        Col("site_name", "Site"),
        Col("priority", "Priority"),
        Col("status", "Status"),
        Col("required_by", "Required by", "date"),
        Col("age_days", "Age (days)", "int"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("approved_qty", "Approved", "qty"),
        Col("issued_qty", "Issued", "qty"),
        Col("pending_qty", "Pending", "qty"),
        Col("central_stock", "Central stock", "qty"),
    ],
)
async def _pending_rfqs(db, ctx, p, scope):
    M, RI = MaterialRequest, MaterialRequestItem
    central = (
        select(StockBalance.material_id, func.sum(StockBalance.qty).label("qty"))
        .join(Store, Store.id == StockBalance.store_id)
        .where(Store.type == StoreType.CENTRAL)
        .group_by(StockBalance.material_id)
        .subquery()
    )
    stmt = (
        select(
            M.request_no,
            Site.name.label("site_name"),
            M.priority,
            M.status,
            M.required_by,
            M.request_date,
            *_mat_cols(),
            RI.approved_qty,
            RI.issued_qty,
            func.coalesce(central.c.qty, 0).label("central_stock"),
        )
        .join(M, M.id == RI.request_id)
        .join(Site, Site.id == M.site_id)
        .join(Material, Material.id == RI.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .outerjoin(central, central.c.material_id == RI.material_id)
        .where(
            M.status.in_([RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED, RfqStatus.PM_APPROVED]),
            func.coalesce(RI.approved_qty, 0) > RI.issued_qty,
            _site_scope(scope, M.site_id, M.project_id) if not scope.store_ids else _store_scope(scope, M.store_id, M.site_id),
        )
        .order_by((M.priority == "URGENT").desc(), M.request_date)
    )
    if p.project_id:
        stmt = stmt.where(M.project_id == p.project_id)
    if p.site_id:
        stmt = stmt.where(M.site_id == p.site_id)
    t = today()
    return [
        {**r, "age_days": (t - r["request_date"]).days, "pending_qty": Decimal(r["approved_qty"] or 0) - Decimal(r["issued_qty"])}
        for r in (await db.execute(stmt)).mappings()
    ]


@report(
    key="non-moving-stock",
    title="Non-moving stock",
    level="store",
    description="Stock with no movement for at least N days.",
    filters=["days", "store_id"],
    requires=("stock.read",),
    columns=[
        Col("store_name", "Store"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("qty", "Qty", "qty"),
        Col("last_movement_at", "Last movement", "datetime"),
        Col("days_idle", "Days idle", "int"),
        Col("value", "Value", "money", True),
    ],
)
async def _non_moving(db, ctx, p, scope):
    cutoff = datetime.combine(today() - timedelta(days=p.days), datetime.min.time())
    stmt = (
        select(Store.name.label("store_name"), *_mat_cols(), StockBalance.qty, StockBalance.value, StockBalance.last_movement_at)
        .join(Store, Store.id == StockBalance.store_id)
        .join(Material, Material.id == StockBalance.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .where(StockBalance.qty > 0, StockBalance.last_movement_at < cutoff, _store_scope(scope, StockBalance.store_id, Store.site_id))
        .order_by(StockBalance.last_movement_at)
    )
    if p.store_id:
        stmt = stmt.where(StockBalance.store_id == p.store_id)
    t = today()
    return [
        {**r, "value": q2(r["value"]), "days_idle": (t - r["last_movement_at"].date()).days if r["last_movement_at"] else None}
        for r in (await db.execute(stmt)).mappings()
    ]


@report(
    key="adjustment-register",
    title="Adjustment & physical count register",
    level="audit",
    description="Approved stock adjustments: opening stock, physical count variances, damage/wastage/theft.",
    filters=["date_from", "date_to", "store_id"],
    requires=("stock.read",),
    columns=[
        Col("adj_no", "Adjustment"),
        Col("adj_date", "Date", "date"),
        Col("store_name", "Store"),
        Col("reason", "Reason"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("system_qty", "System", "qty"),
        Col("physical_qty", "Physical", "qty"),
        Col("qty_delta", "Change", "qty"),
        Col("rate", "Rate", "rate", True),
        Col("loss_value", "Value impact", "money", True),
    ],
)
async def _adjustment_register(db, ctx, p, scope):
    A, AI = StockAdjustment, StockAdjustmentItem
    stmt = (
        select(
            A.adj_no,
            A.adj_date,
            Store.name.label("store_name"),
            A.reason,
            *_mat_cols(),
            AI.system_qty,
            AI.physical_qty,
            AI.qty_delta,
            AI.rate,
        )
        .join(A, A.id == AI.adjustment_id)
        .join(Store, Store.id == A.store_id)
        .join(Material, Material.id == AI.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .where(
            A.status == AdjustmentStatus.APPROVED,
            A.adj_date.between(p.date_from, p.date_to),
            _store_scope(scope, A.store_id, Store.site_id),
        )
        .order_by(A.adj_date, A.adj_no)
    )
    if p.store_id:
        stmt = stmt.where(A.store_id == p.store_id)
    return [{**r, "loss_value": q2(Decimal(r["qty_delta"] or 0) * Decimal(r["rate"] or 0))} for r in (await db.execute(stmt)).mappings()]


@report(
    key="transit-loss",
    title="Transit loss (shortage / damage)",
    level="store",
    description="Issue notes received with shortage or damage.",
    filters=["date_from", "date_to", "project_id", "site_id"],
    requires=("issue.read",),
    columns=[
        Col("issue_no", "Issue note"),
        Col("issue_date", "Date", "date"),
        Col("site_name", "Site"),
        Col("vehicle_no", "Vehicle"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("issued_qty", "Issued", "qty"),
        Col("shortage_qty", "Short", "qty"),
        Col("damaged_qty", "Damaged", "qty"),
        Col("loss_pct", "Loss %", "pct"),
        Col("loss_value", "Loss value", "money", True),
    ],
)
async def _transit_loss(db, ctx, p, scope):
    stmt = (
        select(
            IssueNote.issue_no,
            IssueNote.issue_date,
            Site.name.label("site_name"),
            IssueNote.vehicle_no,
            *_mat_cols(),
            IssueNoteItem.issued_qty,
            IssueNoteItem.shortage_qty,
            IssueNoteItem.damaged_qty,
            IssueNoteItem.rate,
        )
        .join(IssueNote, IssueNote.id == IssueNoteItem.issue_note_id)
        .join(Site, Site.id == IssueNote.site_id)
        .join(Material, Material.id == IssueNoteItem.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .where(
            (IssueNoteItem.shortage_qty + IssueNoteItem.damaged_qty) > 0,
            IssueNote.issue_date.between(p.date_from, p.date_to),
            _site_scope(scope, IssueNote.site_id, IssueNote.project_id)
            if not scope.store_ids
            else _store_scope(scope, IssueNote.from_store_id, IssueNote.site_id),
        )
        .order_by(IssueNote.issue_date)
    )
    if p.project_id:
        stmt = stmt.where(IssueNote.project_id == p.project_id)
    if p.site_id:
        stmt = stmt.where(IssueNote.site_id == p.site_id)
    out = []
    for r in (await db.execute(stmt)).mappings():
        loss = Decimal(r["shortage_qty"]) + Decimal(r["damaged_qty"])
        out.append({**r, "loss_pct": q2(loss / Decimal(r["issued_qty"]) * 100), "loss_value": q2(loss * Decimal(r["rate"] or 0))})
    return out


# ------------------------------------------------------------------ project / site


@report(
    key="project-allocation",
    title="Project allocation vs usage",
    level="project",
    description="BOQ allocation per project vs approved requests and issues.",
    filters=["fy", "project_id"],
    requires=("boq.read",),
    columns=[
        Col("project_name", "Project"),
        Col("sites", "Sites"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("allocated_qty", "Allocated", "qty"),
        Col("requested_qty", "Requested", "qty"),
        Col("approved_qty", "Approved", "qty"),
        Col("issued_qty", "Issued", "qty"),
        Col("remaining_allocation", "Remaining", "qty"),
        Col("utilisation_pct", "Utilised %", "pct"),
    ],
)
async def _project_allocation(db, ctx, p, scope):
    boq = await _approved_or_latest_boq(db, p.fy)
    if boq is None:
        return []
    rows = await allocation_progress(db, boq)
    return [
        r for r in rows if (scope.all or r["project_id"] in scope.project_ids) and (not p.project_id or r["project_id"] == p.project_id)
    ]


@report(
    key="site-stock",
    title="Site stock & days of cover",
    level="site",
    description="Site stock with 7/30-day average daily consumption and days of cover.",
    filters=["site_id"],
    required_filters=("site_id",),
    requires=("site.read",),
    columns=[
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("qty", "Stock", "qty"),
        Col("in_transit", "In transit", "qty"),
        Col("avg_daily_7d", "Avg/day (7d)", "qty"),
        Col("avg_daily_30d", "Avg/day (30d)", "qty"),
        Col("days_of_cover", "Days of cover", "qty"),
        Col("value", "Value", "money", True),
    ],
)
async def _site_stock(db, ctx, p, scope):
    from app.api.v1.site import site_stock_lines

    site = await db.get(Site, p.site_id)
    if site is None or not scope.covers(site_id=site.id, project_id=site.project_id):
        return []
    return [
        {**ln.model_dump(exclude={"material"}), "material_name": ln.material.name, "uom": ln.material.uom}
        for ln in await site_stock_lines(db, site.id)
    ]


@report(
    key="consumption-log",
    title="Consumption log",
    level="site",
    description="Daily consumption entries by site, material and activity.",
    filters=["date_from", "date_to", "project_id", "site_id", "material_id"],
    requires=("site.read",),
    columns=[
        Col("entry_date", "Date", "date"),
        Col("site_name", "Site"),
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("qty", "Qty", "qty"),
        Col("activity", "Activity"),
        Col("recorded_by", "Recorded by"),
        Col("amount", "Value", "money", True),
    ],
)
async def _consumption_log(db, ctx, p, scope):
    from app.models import User

    E = ConsumptionEntry
    stmt = (
        select(E.entry_date, Site.name.label("site_name"), *_mat_cols(), E.qty, E.activity, User.name.label("recorded_by"), E.amount)
        .join(Site, Site.id == E.site_id)
        .join(Material, Material.id == E.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .outerjoin(User, User.id == E.created_by_id)
        .where(E.status == EntryStatus.POSTED, E.entry_date.between(p.date_from, p.date_to), _site_scope(scope, E.site_id, Site.project_id))
        .order_by(E.entry_date.desc(), Site.name)
    )
    for col, val in ((Site.project_id, p.project_id), (E.site_id, p.site_id), (E.material_id, p.material_id)):
        if val:
            stmt = stmt.where(col == val)
    return [dict(r) for r in (await db.execute(stmt)).mappings()]


@report(
    key="rfq-status",
    title="Request status summary",
    level="project",
    description="Material requests by site and status in the period.",
    filters=["date_from", "date_to", "project_id"],
    requires=("rfq.read",),
    columns=[
        Col("project_name", "Project"),
        Col("site_name", "Site"),
        Col("total", "Total", "int"),
        Col("pending_approval", "Pending approval", "int"),
        Col("approved", "Approved / issuing", "int"),
        Col("issued", "Fully issued", "int"),
        Col("rejected", "Rejected / cancelled", "int"),
    ],
)
async def _rfq_status(db, ctx, p, scope):
    M = MaterialRequest

    def n(*states):
        return func.count(case((M.status.in_(states), literal(1))))

    stmt = (
        select(
            Project.name.label("project_name"),
            Site.name.label("site_name"),
            func.count().label("total"),
            n(RfqStatus.SUBMITTED, RfqStatus.PM_APPROVED, RfqStatus.RETURNED).label("pending_approval"),
            n(RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED).label("approved"),
            n(RfqStatus.ISSUED, RfqStatus.CLOSED).label("issued"),
            n(RfqStatus.REJECTED, RfqStatus.CANCELLED).label("rejected"),
        )
        .join(Project, Project.id == M.project_id)
        .join(Site, Site.id == M.site_id)
        .where(M.status != RfqStatus.DRAFT, M.request_date.between(p.date_from, p.date_to), _site_scope(scope, M.site_id, M.project_id))
        .group_by(Project.name, Site.name)
        .order_by(Project.name, Site.name)
    )
    if p.project_id:
        stmt = stmt.where(M.project_id == p.project_id)
    return [dict(r) for r in (await db.execute(stmt)).mappings()]


# ------------------------------------------------------------------ market


@report(
    key="vendor-rates",
    title="Vendor rate comparison",
    level="market",
    description="Latest quoted rate per vendor per material.",
    filters=["material_id", "category_id"],
    requires=("market.read", "finance.view"),
    columns=[
        Col("material_name", "Material"),
        Col("uom", "UOM"),
        Col("vendor_name", "Vendor / source"),
        Col("rate", "Latest rate", "rate", True),
        Col("quoted_on", "Quoted on", "date"),
        Col("valid_till", "Valid till", "date"),
    ],
)
async def _vendor_rates(db, ctx, p, scope):
    from app.models import MarketRate

    R = MarketRate
    stmt = (
        select(
            Material.name.label("material_name"),
            Uom.code.label("uom"),
            func.coalesce(Vendor.name, R.source).label("vendor_name"),
            R.rate,
            R.quoted_on,
            R.valid_till,
        )
        .join(Material, Material.id == R.material_id)
        .join(Uom, Uom.id == Material.base_uom_id)
        .outerjoin(Vendor, Vendor.id == R.vendor_id)
        .order_by(R.material_id, R.vendor_id, R.quoted_on.desc(), R.created_at.desc())
        .distinct(R.material_id, R.vendor_id)
    )
    if p.material_id:
        stmt = stmt.where(R.material_id == p.material_id)
    if p.category_id:
        stmt = stmt.where(Material.category_id == p.category_id)
    rows = [dict(r) for r in (await db.execute(stmt)).mappings()]
    return sorted(rows, key=lambda r: (r["material_name"], r["rate"]))


# ------------------------------------------------------------------ runner


def visible_columns(rep: Report, ctx: UserContext) -> list[Col]:
    return [c for c in rep.columns if ctx.show_finance or not c.finance]


def can_run(rep: Report, ctx: UserContext) -> bool:
    return ctx.has("reports.read") and all(ctx.has(p) for p in rep.requires)


def build_params(rep: Report, raw: dict[str, Any]) -> Params:
    t = today()
    fy = raw.get("fy") or financial_year(t)
    fy_start, fy_end = fy_bounds(fy)
    p = Params(
        fy=fy,
        date_from=raw.get("date_from") or max(fy_start, t - timedelta(days=30))
        if "fy" not in rep.filters
        else raw.get("date_from") or fy_start,
        date_to=raw.get("date_to") or (t if "fy" not in rep.filters else min(fy_end, t)),
        project_id=raw.get("project_id"),
        site_id=raw.get("site_id"),
        store_id=raw.get("store_id"),
        material_id=raw.get("material_id"),
        category_id=raw.get("category_id"),
        vendor_id=raw.get("vendor_id"),
        days=raw.get("days") or 90,
    )
    if p.date_from > p.date_to:
        raise AppError("VALIDATION_ERROR", "date_from must be on or before date_to", 422)
    if (p.date_to - p.date_from).days > 731:
        raise AppError("VALIDATION_ERROR", "Date range is limited to 2 years", 422)
    missing = [f for f in rep.required_filters if getattr(p, f) is None]
    if missing:
        raise AppError("FILTER_REQUIRED", f"Select {', '.join(m.replace('_id', '') for m in missing)} to run this report", 422, missing)
    return p


async def run_report(db: AsyncSession, ctx: UserContext, key: str, raw: dict[str, Any]) -> tuple[Report, Params, list[Col], list[dict]]:
    rep = REGISTRY.get(key)
    if rep is None:
        raise AppError("NOT_FOUND", "Report not found", 404)
    if not can_run(rep, ctx):
        raise AppError("FORBIDDEN", "You don't have access to this report", 403)
    params = build_params(rep, raw)
    rows = await rep.fn(db, ctx, params, ctx.scope("reports.read"))
    cols = visible_columns(rep, ctx)
    keys = [c.key for c in cols]
    return rep, params, cols, [{k: _plain(r.get(k)) for k in keys} for r in rows]


def _plain(v: Any) -> Any:
    """JSON-friendly scalars: Decimal → float (numbers for React/Flutter), UUID → str."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def totals(cols: list[Col], rows: list[dict]) -> dict[str, float]:
    out = {}
    for c in cols:
        if c.type in ("qty", "money", "int") and not c.key.startswith(("days", "age", "hours", "avg", "reorder", "balance")):
            vals = [r[c.key] for r in rows if isinstance(r.get(c.key), (int, float, Decimal))]
            if vals and len({r.get("uom") for r in rows}) <= 1 or c.type in ("money", "int"):
                out[c.key] = float(sum(vals)) if vals else 0.0
    return out
