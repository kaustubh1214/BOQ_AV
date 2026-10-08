import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import DB, Perm, respond
from app.core.errors import AppError
from app.core.responses import Envelope
from app.services.common import audit, today
from app.services.exports import to_xlsx
from app.services.reports import REGISTRY, can_run, run_report, totals, visible_columns

router = APIRouter(prefix="/reports", tags=["reports"])


class ColumnOut(BaseModel):
    key: str
    label: str
    type: str


class ReportMeta(BaseModel):
    key: str
    title: str
    level: str
    description: str
    filters: list[str]
    required_filters: list[str]
    columns: list[ColumnOut]


class ReportOut(ReportMeta):
    params: dict[str, Any]
    rows: list[dict[str, Any]]
    totals: dict[str, float]
    row_count: int


class ReportQuery(BaseModel):
    fy: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}$")] = None
    date_from: date | None = None
    date_to: date | None = None
    project_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    material_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    days: Annotated[int | None, Field(ge=1, le=3650)] = None


def _meta(rep, ctx) -> ReportMeta:
    return ReportMeta(
        key=rep.key,
        title=rep.title,
        level=rep.level,
        description=rep.description,
        filters=rep.filters,
        required_filters=list(rep.required_filters),
        columns=[ColumnOut(key=c.key, label=c.label, type=c.type) for c in visible_columns(rep, ctx)],
    )


@router.get("", response_model=Envelope[list[ReportMeta]], summary="Reports this user can run")
async def list_reports(ctx: Perm("reports.read")):
    return respond(ctx, [_meta(r, ctx) for r in REGISTRY.values() if can_run(r, ctx)])


@router.get("/{key}", response_model=Envelope[ReportOut])
async def get_report(key: str, db: DB, ctx: Perm("reports.read"), q: Annotated[ReportQuery, Query()]):
    rep, params, cols, rows = await run_report(db, ctx, key, q.model_dump())
    out = ReportOut(
        **_meta(rep, ctx).model_dump(),
        params={k: v for k, v in params.__dict__.items() if k in rep.filters},
        rows=rows,
        totals=totals(cols, rows),
        row_count=len(rows),
    )
    # Columns were already filtered; values are plain → no scrub needed beyond the column filter.
    return {"success": True, "data": out, "meta": {}}


@router.get("/{key}/export", summary="Download as Excel (.xlsx)", response_class=Response)
async def export_report(key: str, db: DB, ctx: Perm("export"), q: Annotated[ReportQuery, Query()]):
    if not ctx.has("reports.read"):
        raise AppError("FORBIDDEN", "You don't have access to reports", 403)
    rep, params, cols, rows = await run_report(db, ctx, key, q.model_dump())
    filters = {k: v for k, v in params.__dict__.items() if k in rep.filters}
    data = to_xlsx(rep.title, filters, cols, rows, totals(cols, rows), ctx.user.name)
    await audit(db, ctx, "EXPORT", "report", None, f"{rep.key} ({len(rows)} rows)")
    await db.commit()
    filename = f"{rep.key}-{today().isoformat()}.xlsx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
