"""One dashboard endpoint; sections are included according to the caller's permissions and scope,
so management, store, project and site users each get their own level of view."""

import uuid
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, Perm
from app.core.responses import Envelope, ok
from app.models import (
    Grn,
    IssueNote,
    IssueNoteItem,
    Material,
    MaterialRequest,
    Project,
    ReturnNote,
    StockAdjustment,
    StockBalance,
    Store,
)
from app.models.enums import AdjustmentStatus, GrnStatus, IssueStatus, ReturnStatus, RfqStatus, StoreType
from app.services.boq import approved_boq_for_fy, boq_progress
from app.services.common import financial_year, fy_bounds, q2
from app.services.context import UserContext, scope_filter

router = APIRouter(tags=["dashboard"])


class Kpi(BaseModel):
    key: str
    label: str
    value: float | None
    format: str  # number | money | pct
    hint: str | None = None


class Task(BaseModel):
    key: str
    label: str
    count: int
    link: str


class ChartPoint(BaseModel):
    label: str
    values: dict[str, float | None]


class DashboardOut(BaseModel):
    financial_year: str
    boq_no: str | None
    kpis: list[Kpi]
    tasks: list[Task]
    boq_chart: list[ChartPoint]
    project_chart: list[ChartPoint]
    low_stock: list[dict]


async def _count(db, stmt) -> int:
    return (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()


async def _tasks(db, ctx: UserContext) -> list[Task]:
    M, N = MaterialRequest, IssueNote
    tasks: list[Task] = []
    if ctx.has("rfq.approve_store"):
        n = await _count(
            db,
            select(M.id).where(
                M.status.in_([RfqStatus.SUBMITTED, RfqStatus.PM_APPROVED]),
                scope_filter(ctx.scope("rfq.approve_store"), store_cols=(M.store_id,)),
            ),
        )
        tasks.append(Task(key="rfq_store", label="Site requests awaiting your approval", count=n, link="/rfqs?queue=store"))
    if ctx.has("issue.create"):
        n = await _count(
            db,
            select(M.id).where(
                M.status.in_([RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED]),
                scope_filter(ctx.scope("issue.create"), store_cols=(M.store_id,)),
            ),
        )
        tasks.append(Task(key="rfq_issue", label="Approved requests to issue", count=n, link="/rfqs?queue=issue"))
    if ctx.has("issue.post"):
        n = await _count(
            db, select(N.id).where(N.status == IssueStatus.DRAFT, scope_filter(ctx.scope("issue.post"), store_cols=(N.from_store_id,)))
        )
        tasks.append(Task(key="issue_post", label="Draft issue notes to dispatch", count=n, link="/issue-notes?queue=post"))
    if ctx.has("issue.receive"):
        n = await _count(
            db,
            select(N.id).where(
                N.status == IssueStatus.ISSUED,
                scope_filter(ctx.scope("issue.receive"), store_cols=(N.to_store_id,), site_col=N.site_id, project_col=N.project_id),
            ),
        )
        tasks.append(Task(key="issue_receive", label="Deliveries to receive at site", count=n, link="/issue-notes?queue=receive"))
    if ctx.has("grn.post"):
        n = await _count(
            db, select(Grn.id).where(Grn.status == GrnStatus.DRAFT, scope_filter(ctx.scope("grn.post"), store_cols=(Grn.store_id,)))
        )
        tasks.append(Task(key="grn_post", label="Draft GRNs to post", count=n, link="/grns?status=DRAFT"))
    if ctx.has("return.receive"):
        n = await _count(
            db,
            select(ReturnNote.id).where(
                ReturnNote.status == ReturnStatus.DISPATCHED,
                scope_filter(ctx.scope("return.receive"), store_cols=(ReturnNote.to_store_id,)),
            ),
        )
        tasks.append(Task(key="return_receive", label="Site returns to receive", count=n, link="/returns?queue=receive"))
    if ctx.has("adjustment.approve"):
        A = StockAdjustment
        n = await _count(
            db,
            select(A.id).where(
                A.status == AdjustmentStatus.SUBMITTED, scope_filter(ctx.scope("adjustment.approve"), store_cols=(A.store_id,))
            ),
        )
        tasks.append(Task(key="adj_approve", label="Stock adjustments to approve", count=n, link="/adjustments?status=SUBMITTED"))
    if ctx.has("boq.approve"):
        from app.models import Boq
        from app.models.enums import BoqStatus

        n = await _count(db, select(Boq.id).where(Boq.status == BoqStatus.SUBMITTED))
        tasks.append(Task(key="boq_approve", label="BOQs awaiting approval", count=n, link="/boqs?status=SUBMITTED"))
    if ctx.has("rfq.create"):
        n = await _count(db, select(M.id).where(M.created_by_id == ctx.user_id, M.status == RfqStatus.RETURNED))
        tasks.append(Task(key="rfq_returned", label="Your requests returned for changes", count=n, link="/rfqs?status=RETURNED"))
    return tasks


@router.get("/dashboard", response_model=Envelope[DashboardOut])
async def dashboard(db: DB, ctx: Perm("reports.read")):
    fy = financial_year()
    kpis: list[Kpi] = []
    boq_chart: list[ChartPoint] = []
    project_chart: list[ChartPoint] = []
    low_stock: list[dict] = []
    boq = await approved_boq_for_fy(db, fy) if ctx.has("boq.read") else None

    if boq is not None:
        lines = await boq_progress(db, boq)
        est = sum((Decimal(ln["estimated_value"] or 0) for ln in lines), Decimal(0))
        proc = sum((Decimal(ln["procured_value"]) for ln in lines), Decimal(0))
        planned_lines = len(lines)
        fully = sum(1 for ln in lines if ln["pending_to_procure"] == 0)
        if ctx.show_finance:
            kpis.append(Kpi(key="boq_value", label="BOQ budget", value=float(est), format="money", hint=boq.boq_no))
            kpis.append(
                Kpi(
                    key="procured_value",
                    label="Procured so far",
                    value=float(proc),
                    format="money",
                    hint=f"{q2(proc / est * 100) if est else 0}% of budget",
                )
            )
        kpis.append(Kpi(key="boq_lines", label="Materials fully procured", value=fully, format="number", hint=f"of {planned_lines} in BOQ"))
        top = sorted(lines, key=lambda ln: ln["estimated_value"] or 0, reverse=True)[:8]
        boq_chart = [
            ChartPoint(
                label=ln["material_name"],
                values={
                    "procured_pct": float(ln["procurement_pct"] or 0),
                    "issued_pct": float(ln["utilisation_pct"] or 0),
                    "pending_pct": float(100 - min(Decimal(100), Decimal(ln["procurement_pct"] or 0))),
                },
            )
            for ln in top
        ]

    if ctx.has("stock.read"):
        scope = ctx.scope("stock.read")
        B = StockBalance
        base = (
            select(B, Store, Material)
            .join(Store, Store.id == B.store_id)
            .join(Material, Material.id == B.material_id)
            .where(scope_filter(scope, store_cols=(B.store_id,), site_col=Store.site_id))
        )
        if ctx.show_finance:
            val = (
                await db.execute(
                    select(func.coalesce(func.sum(B.value), 0))
                    .join(Store, Store.id == B.store_id)
                    .where(Store.type == StoreType.CENTRAL, scope_filter(scope, store_cols=(B.store_id,), site_col=Store.site_id))
                )
            ).scalar_one()
            kpis.append(Kpi(key="stock_value", label="Central stock value", value=float(val), format="money"))
        rows = (
            (
                await db.execute(
                    base.where(Store.type == StoreType.CENTRAL, Material.reorder_level > 0, B.qty < Material.reorder_level)
                    .order_by(Material.name)
                    .limit(10)
                )
            )
            .unique()
            .all()
        )
        low_stock = [
            {"material": m.name, "uom": m.base_uom.code, "qty": float(b.qty), "reorder_level": float(m.reorder_level), "store": s.name}
            for b, s, m in rows
        ]
        kpis.append(Kpi(key="low_stock", label="Below reorder level", value=len(low_stock), format="number"))

    if ctx.has("issue.read"):
        N = IssueNote
        scope = ctx.scope("issue.read")
        in_transit = await _count(
            db,
            select(N.id).where(
                N.status == IssueStatus.ISSUED,
                scope_filter(scope, store_cols=(N.from_store_id, N.to_store_id), site_col=N.site_id, project_col=N.project_id),
            ),
        )
        kpis.append(Kpi(key="in_transit", label="Issue notes in transit", value=in_transit, format="number"))
        start, end = fy_bounds(fy)
        rows = await db.execute(
            select(Project.name, func.sum(IssueNoteItem.amount), func.count(func.distinct(N.id)))
            .join(N, N.id == IssueNoteItem.issue_note_id)
            .join(Project, Project.id == N.project_id)
            .where(
                N.status.in_([IssueStatus.ISSUED, IssueStatus.RECEIVED, IssueStatus.PARTIALLY_RECEIVED]),
                N.issue_date.between(start, end),
                scope_filter(scope, store_cols=(N.from_store_id, N.to_store_id), site_col=N.site_id, project_col=N.project_id),
            )
            .group_by(Project.name)
            .order_by(func.sum(IssueNoteItem.amount).desc())
        )
        project_chart = [
            ChartPoint(label=name, values={"issued_value": float(v or 0) if ctx.show_finance else None, "issue_notes": float(n)})
            for name, v, n in rows
        ]

    if ctx.has("rfq.read"):
        M = MaterialRequest
        open_rfqs = await _count(
            db,
            select(M.id).where(
                M.status.in_([RfqStatus.SUBMITTED, RfqStatus.PM_APPROVED, RfqStatus.STORE_APPROVED, RfqStatus.PARTIALLY_ISSUED]),
                scope_filter(ctx.scope("rfq.read"), store_cols=(M.store_id,), site_col=M.site_id, project_col=M.project_id),
            ),
        )
        kpis.append(Kpi(key="open_rfqs", label="Open material requests", value=open_rfqs, format="number"))

    # Finance-only KPIs/series are already omitted above for users without finance.view, so no generic
    # scrub here (it would also blank the KPI field that is literally named "value").
    return ok(
        DashboardOut(
            financial_year=fy,
            boq_no=boq.boq_no if boq else None,
            kpis=kpis,
            tasks=await _tasks(db, ctx),
            boq_chart=boq_chart,
            project_chart=project_chart,
            low_stock=low_stock,
        )
    )


_ = uuid
