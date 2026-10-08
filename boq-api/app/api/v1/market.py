"""Step 1 of the sketch: Market Research. Vendor quotes / surveys per material → benchmark rates for the BOQ."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DB, PagingDep, Perm, get_or_404, respond, respond_page
from app.core.errors import AppError
from app.core.responses import Envelope, PageEnvelope
from app.models import MarketRate, Material, Vendor
from app.models.enums import RateSource
from app.schemas.common import In, OptNum, Out, Rate
from app.schemas.docs import MaterialBrief, mat_brief
from app.services.common import audit, bump, check_version, q4, snapshot, to_base_uom, today
from app.services.lookups import materials_by_id

router = APIRouter(prefix="/market-rates", tags=["market research"])

BENCHMARK_DAYS = 180


class MarketRateIn(In):
    material_id: uuid.UUID
    vendor_id: uuid.UUID | None = None
    source: RateSource = RateSource.VENDOR_QUOTE
    rate: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
    uom_id: uuid.UUID | None = Field(None, description="Rate is per this UOM; converted to the material's base UOM")
    quoted_on: date | None = None
    valid_till: date | None = None
    location: Annotated[str | None, Field(max_length=120)] = None
    remarks: Annotated[str | None, Field(max_length=1000)] = None


class MarketRateUpdate(In):
    rate: Rate | None = None
    valid_till: date | None = None
    location: Annotated[str | None, Field(max_length=120)] = None
    remarks: Annotated[str | None, Field(max_length=1000)] = None
    version: int | None = None


class MarketRateOut(Out):
    id: uuid.UUID
    material: MaterialBrief
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    source: str
    rate: OptNum
    quoted_on: date
    valid_till: date | None
    location: str | None
    remarks: str | None
    version: int
    created_at: datetime


class BenchmarkOut(Out):
    material_id: uuid.UUID
    material_name: str
    uom: str
    quotes: int
    last_rate: OptNum
    min_rate: OptNum
    max_rate: OptNum
    avg_rate: OptNum
    last_quoted_on: date | None


class VendorRateOut(Out):
    vendor_id: uuid.UUID | None
    vendor_name: str
    rate: OptNum
    quoted_on: date
    valid_till: date | None
    source: str


class TrendPoint(Out):
    quoted_on: date
    rate: OptNum
    vendor_name: str | None


class CompareOut(Out):
    material: MaterialBrief
    benchmark: BenchmarkOut
    vendors: list[VendorRateOut]
    trend: list[TrendPoint]


def _out(r: MarketRate) -> MarketRateOut:
    return MarketRateOut(
        id=r.id,
        material=mat_brief(r.material),
        vendor_id=r.vendor_id,
        vendor_name=r.vendor.name if r.vendor else None,
        source=r.source,
        rate=r.rate,
        quoted_on=r.quoted_on,
        valid_till=r.valid_till,
        location=r.location,
        remarks=r.remarks,
        version=r.version,
        created_at=r.created_at,
    )


async def benchmarks(db, material_ids: list[uuid.UUID], as_of: date | None = None) -> dict[uuid.UUID, dict]:
    """last / min / max / avg rate per material over the trailing BENCHMARK_DAYS."""
    if not material_ids:
        return {}
    since = (as_of or today()) - timedelta(days=BENCHMARK_DAYS)
    agg = await db.execute(
        select(
            MarketRate.material_id,
            func.count(),
            func.min(MarketRate.rate),
            func.max(MarketRate.rate),
            func.avg(MarketRate.rate),
            func.max(MarketRate.quoted_on),
        )
        .where(MarketRate.material_id.in_(material_ids), MarketRate.quoted_on >= since)
        .group_by(MarketRate.material_id)
    )
    out = {
        mid: {"quotes": n, "min_rate": mn, "max_rate": mx, "avg_rate": q4(av), "last_quoted_on": last} for mid, n, mn, mx, av, last in agg
    }
    last_rates = await db.execute(
        select(MarketRate.material_id, MarketRate.rate)
        .where(MarketRate.material_id.in_(material_ids), MarketRate.quoted_on >= since)
        .order_by(MarketRate.material_id, MarketRate.quoted_on.desc(), MarketRate.created_at.desc())
        .distinct(MarketRate.material_id)
    )
    for mid, rate in last_rates:
        out[mid]["last_rate"] = rate
    return out


@router.get("", response_model=PageEnvelope[MarketRateOut])
async def list_rates(
    db: DB,
    ctx: Perm("market.read"),
    paging: PagingDep,
    material_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    source: RateSource | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    stmt = select(MarketRate)
    if material_id:
        stmt = stmt.where(MarketRate.material_id == material_id)
    if vendor_id:
        stmt = stmt.where(MarketRate.vendor_id == vendor_id)
    if source:
        stmt = stmt.where(MarketRate.source == source)
    if date_from:
        stmt = stmt.where(MarketRate.quoted_on >= date_from)
    if date_to:
        stmt = stmt.where(MarketRate.quoted_on <= date_to)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                stmt.order_by(MarketRate.quoted_on.desc(), MarketRate.created_at.desc()).offset(paging.offset).limit(paging.limit)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return respond_page(ctx, [_out(r) for r in rows], total, paging)


@router.post("", response_model=Envelope[MarketRateOut], status_code=201)
async def create_rate(body: MarketRateIn, db: DB, ctx: Perm("market.manage")):
    material = (await materials_by_id(db, [body.material_id]))[body.material_id]
    if body.vendor_id and await db.get(Vendor, body.vendor_id) is None:
        raise AppError("INVALID_REFERENCE", "Vendor not found", 422)
    _, rate = await to_base_uom(db, material, Decimal(1), body.rate, body.uom_id)
    r = MarketRate(
        id=uuid.uuid4(),
        material_id=material.id,
        vendor_id=body.vendor_id,
        source=body.source,
        rate=rate,
        quoted_on=body.quoted_on or today(),
        valid_till=body.valid_till,
        location=body.location,
        remarks=body.remarks,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    db.add(r)
    await db.flush()
    await audit(db, ctx, "CREATE", "market_rate", r.id, f"{material.name} @ {rate}", None, snapshot(r))
    await db.commit()
    return respond(ctx, _out(await get_or_404(db, MarketRate, r.id)))


@router.patch("/{rate_id}", response_model=Envelope[MarketRateOut])
async def update_rate(rate_id: uuid.UUID, body: MarketRateUpdate, db: DB, ctx: Perm("market.manage")):
    r = await get_or_404(db, MarketRate, rate_id, "Market rate", lock=True)
    data = body.model_dump(exclude_unset=True)
    check_version(r, data.pop("version", None))
    before = snapshot(r)
    for k, v in data.items():
        setattr(r, k, v)
    bump(r, ctx)
    await audit(db, ctx, "UPDATE", "market_rate", r.id, None, before, snapshot(r))
    await db.commit()
    return respond(ctx, _out(await get_or_404(db, MarketRate, r.id)))


@router.get("/benchmark", response_model=Envelope[list[BenchmarkOut]], summary=f"Benchmark (last {BENCHMARK_DAYS} days) per material")
async def benchmark(db: DB, ctx: Perm("market.read", "boq.read"), material_id: Annotated[list[uuid.UUID], Query()]):
    mats = (await db.execute(select(Material).where(Material.id.in_(material_id)))).unique().scalars().all()
    stats = await benchmarks(db, [m.id for m in mats])
    out = [
        BenchmarkOut(
            material_id=m.id,
            material_name=m.name,
            uom=m.base_uom.code,
            quotes=stats.get(m.id, {}).get("quotes", 0),
            last_rate=stats.get(m.id, {}).get("last_rate"),
            min_rate=stats.get(m.id, {}).get("min_rate"),
            max_rate=stats.get(m.id, {}).get("max_rate"),
            avg_rate=stats.get(m.id, {}).get("avg_rate"),
            last_quoted_on=stats.get(m.id, {}).get("last_quoted_on"),
        )
        for m in mats
    ]
    return respond(ctx, out)


@router.get("/compare", response_model=Envelope[CompareOut], summary="Vendor comparison + rate trend for one material")
async def compare(db: DB, ctx: Perm("market.read"), material_id: uuid.UUID):
    material = await get_or_404(db, Material, material_id, "Material")
    stats = (await benchmarks(db, [material.id])).get(material.id, {})
    latest = await db.execute(
        select(MarketRate)
        .where(MarketRate.material_id == material.id)
        .order_by(MarketRate.vendor_id, MarketRate.quoted_on.desc(), MarketRate.created_at.desc())
        .distinct(MarketRate.vendor_id)
    )
    vendors = sorted(
        (
            VendorRateOut(
                vendor_id=r.vendor_id,
                vendor_name=r.vendor.name if r.vendor else f"Market ({r.source.replace('_', ' ').title()})",
                rate=r.rate,
                quoted_on=r.quoted_on,
                valid_till=r.valid_till,
                source=r.source,
            )
            for r in latest.unique().scalars()
        ),
        key=lambda v: v.rate or 0,
    )
    trend_rows = await db.execute(
        select(MarketRate.quoted_on, MarketRate.rate, Vendor.name)
        .outerjoin(Vendor, Vendor.id == MarketRate.vendor_id)
        .where(MarketRate.material_id == material.id)
        .order_by(MarketRate.quoted_on)
        .limit(500)
    )
    out = CompareOut(
        material=mat_brief(material),
        benchmark=BenchmarkOut(
            material_id=material.id,
            material_name=material.name,
            uom=material.base_uom.code,
            quotes=stats.get("quotes", 0),
            last_rate=stats.get("last_rate"),
            min_rate=stats.get("min_rate"),
            max_rate=stats.get("max_rate"),
            avg_rate=stats.get("avg_rate"),
            last_quoted_on=stats.get("last_quoted_on"),
        ),
        vendors=vendors,
        trend=[TrendPoint(quoted_on=d, rate=r, vendor_name=v) for d, r, v in trend_rows],
    )
    return respond(ctx, out)
