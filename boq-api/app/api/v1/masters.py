"""Master data CRUD. One small factory builds list/get/create/update/deactivate for each master,
so every master behaves the same for the React and Flutter clients (search, paging, updated_since)."""

import csv
import io
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, File, Query, UploadFile
from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator
from sqlalchemy import func, or_, select

from app.api.deps import DB, Paging, Perm, get_or_404, respond, respond_page
from app.core.errors import AppError
from app.core.responses import Envelope, PageEnvelope
from app.models import Material, MaterialCategory, Project, Site, Store, Uom, UomConversion, Vendor
from app.models.enums import ProjectStatus, StoreType
from app.schemas.common import In, Num, OptNum, Out, Ref
from app.services.common import audit, bump, check_version, snapshot
from app.services.context import UserContext

router = APIRouter(prefix="/masters", tags=["masters"])

Code = Annotated[str, Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$")]
Name = Annotated[str, Field(min_length=1, max_length=160)]


class MasterOutBase(Out):
    id: uuid.UUID
    is_active: bool
    version: int
    updated_at: datetime


# ------------------------------------------------------------------ schemas


class CategoryIn(In):
    code: Code
    name: Name
    parent_id: uuid.UUID | None = None
    default_uom_id: uuid.UUID | None = None
    default_hsn_code: Annotated[str | None, Field(max_length=12)] = None
    default_gst_rate: Annotated[Decimal | None, Field(ge=0, le=100)] = None


class CategoryUpdate(In):
    name: Name | None = None
    parent_id: uuid.UUID | None = None
    default_uom_id: uuid.UUID | None = None
    default_hsn_code: Annotated[str | None, Field(max_length=12)] = None
    default_gst_rate: Annotated[Decimal | None, Field(ge=0, le=100)] = None
    is_active: bool | None = None
    version: int | None = None


class CategoryOut(MasterOutBase):
    code: str
    name: str
    parent_id: uuid.UUID | None
    default_uom_id: uuid.UUID | None = None
    default_hsn_code: str | None = None
    default_gst_rate: OptNum = None


class UomIn(In):
    code: Annotated[str, Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_]+$")]
    name: Annotated[str, Field(min_length=1, max_length=60)]


class UomUpdate(In):
    name: Annotated[str | None, Field(min_length=1, max_length=60)] = None
    is_active: bool | None = None
    version: int | None = None


class UomOut(MasterOutBase):
    code: str
    name: str


class ConversionIn(In):
    material_id: uuid.UUID | None = None
    from_uom_id: uuid.UUID
    to_uom_id: uuid.UUID
    factor: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class ConversionUpdate(In):
    factor: Annotated[Decimal | None, Field(gt=0, max_digits=18, decimal_places=6)] = None
    is_active: bool | None = None
    version: int | None = None


class ConversionOut(MasterOutBase):
    material_id: uuid.UUID | None
    from_uom_id: uuid.UUID
    to_uom_id: uuid.UUID
    factor: Num


class MaterialIn(In):
    code: Code
    name: Name
    category_id: uuid.UUID
    base_uom_id: uuid.UUID
    hsn_code: Annotated[str | None, Field(max_length=12)] = None
    gst_rate: Annotated[Decimal, Field(ge=0, le=100)] = Decimal("18")
    specification: Annotated[str | None, Field(max_length=2000)] = None
    reorder_level: Annotated[Decimal, Field(ge=0)] = Decimal("0")


class MaterialUpdate(In):
    name: Name | None = None
    category_id: uuid.UUID | None = None
    hsn_code: Annotated[str | None, Field(max_length=12)] = None
    gst_rate: Annotated[Decimal | None, Field(ge=0, le=100)] = None
    specification: Annotated[str | None, Field(max_length=2000)] = None
    reorder_level: Annotated[Decimal | None, Field(ge=0)] = None
    is_active: bool | None = None
    version: int | None = None


class MaterialOut(MasterOutBase):
    code: str
    name: str
    category: Ref
    base_uom: Ref
    hsn_code: str | None
    gst_rate: Num
    specification: str | None
    reorder_level: Num


_GSTIN = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
_PAN = r"^[A-Z]{5}[0-9]{4}[A-Z]$"


class VendorIn(In):
    code: Code
    name: Name
    gstin: Annotated[str | None, Field(pattern=_GSTIN)] = None
    pan: Annotated[str | None, Field(pattern=_PAN)] = None
    contact_person: Annotated[str | None, Field(max_length=120)] = None
    phone: Annotated[str | None, Field(max_length=20)] = None
    email: EmailStr | None = None
    address: Annotated[str | None, Field(max_length=1000)] = None

    @field_validator("gstin", "pan", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() or None if isinstance(v, str) else v


class VendorUpdate(In):
    name: Name | None = None
    gstin: Annotated[str | None, Field(pattern=_GSTIN)] = None
    pan: Annotated[str | None, Field(pattern=_PAN)] = None
    contact_person: Annotated[str | None, Field(max_length=120)] = None
    phone: Annotated[str | None, Field(max_length=20)] = None
    email: EmailStr | None = None
    address: Annotated[str | None, Field(max_length=1000)] = None
    is_active: bool | None = None
    version: int | None = None

    _upper = field_validator("gstin", "pan", mode="before")(VendorIn._upper.__func__)


class VendorOut(MasterOutBase):
    code: str
    name: str
    gstin: str | None
    pan: str | None
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None


class ProjectIn(In):
    code: Code
    name: Name
    city: Annotated[str | None, Field(max_length=80)] = None
    address: Annotated[str | None, Field(max_length=1000)] = None
    start_date: date | None = None
    end_date: date | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE


class ProjectUpdate(In):
    name: Name | None = None
    city: Annotated[str | None, Field(max_length=80)] = None
    address: Annotated[str | None, Field(max_length=1000)] = None
    start_date: date | None = None
    end_date: date | None = None
    status: ProjectStatus | None = None
    is_active: bool | None = None
    version: int | None = None


class ProjectOut(MasterOutBase):
    code: str
    name: str
    city: str | None
    address: str | None
    start_date: date | None
    end_date: date | None
    status: str


class SiteIn(In):
    project_id: uuid.UUID
    code: Code
    name: Name
    address: Annotated[str | None, Field(max_length=1000)] = None
    lat: Annotated[Decimal | None, Field(ge=-90, le=90)] = None
    lng: Annotated[Decimal | None, Field(ge=-180, le=180)] = None


class SiteUpdate(In):
    name: Name | None = None
    address: Annotated[str | None, Field(max_length=1000)] = None
    lat: Annotated[Decimal | None, Field(ge=-90, le=90)] = None
    lng: Annotated[Decimal | None, Field(ge=-180, le=180)] = None
    status: ProjectStatus | None = None
    is_active: bool | None = None
    version: int | None = None


class SiteOut(MasterOutBase):
    project_id: uuid.UUID
    project: Ref
    code: str
    name: str
    address: str | None
    lat: OptNum
    lng: OptNum
    status: str
    store_id: uuid.UUID | None = None


class StoreIn(In):
    code: Code
    name: Name


class StoreUpdate(In):
    name: Name | None = None
    is_active: bool | None = None
    version: int | None = None


class StoreOut(MasterOutBase):
    code: str
    name: str
    type: str
    site_id: uuid.UUID | None


# ------------------------------------------------------------------ factory


class ListQuery(BaseModel):
    q: str | None = None
    active: bool | None = True
    updated_since: Annotated[datetime | None, Field(description="Delta sync for mobile caches")] = None
    page: Annotated[int, Field(ge=1)] = 1
    limit: Annotated[int, Field(ge=1, le=500)] = 25


class CategoryQuery(ListQuery):
    parent_id: uuid.UUID | None = None


class ConversionQuery(ListQuery):
    material_id: uuid.UUID | None = None


class MaterialQuery(ListQuery):
    category_id: uuid.UUID | None = None


class SiteQuery(ListQuery):
    project_id: uuid.UUID | None = None


class StoreQuery(ListQuery):
    type: StoreType | None = None


def _crud(
    path: str,
    model: Any,
    in_schema: type[BaseModel],
    update_schema: type[BaseModel],
    out_schema: type[BaseModel],
    *,
    manage_perm: str = "masters.manage",
    label: str,
    search: tuple = ("code", "name"),
    query_model: type[ListQuery] = ListQuery,
    to_out=None,
    after_create=None,
    create_defaults: dict | None = None,
):
    to_out = to_out or (lambda db, obj: _async_identity(out_schema.model_validate(obj)))
    extra_filters = [f for f in query_model.model_fields if f not in ListQuery.model_fields]

    async def list_items(db: DB, ctx: Perm("masters.read"), params: Annotated[query_model, Query()]):  # type: ignore[valid-type]
        stmt = select(model)
        if params.q and search:
            like = f"%{params.q.strip()}%"
            stmt = stmt.where(or_(*[getattr(model, f).ilike(like) for f in search]))
        if params.active is not None:
            stmt = stmt.where(model.is_active.is_(params.active))
        if params.updated_since is not None:
            stmt = stmt.where(model.updated_at > params.updated_since)
        for f in extra_filters:
            if (val := getattr(params, f)) is not None:
                stmt = stmt.where(getattr(model, f) == val)
        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        order = model.name if hasattr(model, "name") else model.created_at
        offset = (params.page - 1) * params.limit
        rows = (await db.execute(stmt.order_by(order).offset(offset).limit(params.limit))).unique().scalars().all()
        paging = Paging(page=params.page, limit=params.limit)
        return respond_page(ctx, [await to_out(db, r) for r in rows], total, paging)

    router.add_api_route(
        f"/{path}", list_items, methods=["GET"], response_model=PageEnvelope[out_schema], name=f"list_{path}", summary=f"List {label}s"
    )

    async def get_item(item_id: uuid.UUID, db: DB, ctx: Perm("masters.read")):
        return respond(ctx, await to_out(db, await get_or_404(db, model, item_id, label)))

    router.add_api_route(
        f"/{path}/{{item_id}}", get_item, methods=["GET"], response_model=Envelope[out_schema], name=f"get_{path}", summary=f"Get {label}"
    )

    async def create_item(body: in_schema, db: DB, ctx: Perm(manage_perm)):  # type: ignore[valid-type]
        obj = model(id=uuid.uuid4(), **(create_defaults or {}), **body.model_dump(), created_by_id=ctx.user_id, updated_by_id=ctx.user_id)
        db.add(obj)
        await db.flush()
        if after_create:
            await after_create(db, ctx, obj)
        await audit(db, ctx, "CREATE", path, obj.id, f"{label} {getattr(obj, 'code', '')}", None, snapshot(obj))
        await db.commit()
        return respond(ctx, await to_out(db, await get_or_404(db, model, obj.id, label)))

    router.add_api_route(
        f"/{path}",
        create_item,
        methods=["POST"],
        status_code=201,
        response_model=Envelope[out_schema],
        name=f"create_{path}",
        summary=f"Create {label}",
    )

    async def update_item(item_id: uuid.UUID, body: update_schema, db: DB, ctx: Perm(manage_perm)):  # type: ignore[valid-type]
        obj = await get_or_404(db, model, item_id, label, lock=True)
        data = body.model_dump(exclude_unset=True)
        check_version(obj, data.pop("version", None))
        before = snapshot(obj)
        cols = model.__table__.columns
        for k, v in data.items():
            if v is None and k in cols and not cols[k].nullable:
                continue  # explicit null on a required column = "leave unchanged"
            setattr(obj, k, v)
        bump(obj, ctx)
        await audit(db, ctx, "UPDATE", path, obj.id, None, before, snapshot(obj))
        await db.commit()
        return respond(ctx, await to_out(db, await get_or_404(db, model, obj.id, label)))

    router.add_api_route(
        f"/{path}/{{item_id}}",
        update_item,
        methods=["PATCH"],
        response_model=Envelope[out_schema],
        name=f"update_{path}",
        summary=f"Update {label}",
    )

    async def deactivate_item(item_id: uuid.UUID, db: DB, ctx: Perm(manage_perm)):
        obj = await get_or_404(db, model, item_id, label, lock=True)
        before = snapshot(obj)
        obj.is_active = False
        bump(obj, ctx)
        await audit(db, ctx, "DEACTIVATE", path, obj.id, None, before, snapshot(obj))
        await db.commit()
        return respond(ctx, await to_out(db, await get_or_404(db, model, obj.id, label)))

    router.add_api_route(
        f"/{path}/{{item_id}}",
        deactivate_item,
        methods=["DELETE"],
        response_model=Envelope[out_schema],
        name=f"deactivate_{path}",
        summary=f"Deactivate {label} (soft delete)",
    )


async def _async_identity(x):
    return x


# ------------------------------------------------------------------ custom serialisers / hooks


async def _site_out(db, site: Site) -> SiteOut:
    store_id = (await db.execute(select(Store.id).where(Store.site_id == site.id))).scalar_one_or_none()
    out = SiteOut.model_validate(site)
    out.store_id = store_id
    return out


async def _create_site_store(db, ctx: UserContext, site: Site) -> None:
    project = await db.get(Project, site.project_id)
    if project is None:
        raise AppError("INVALID_REFERENCE", "Project not found", 422)
    db.add(
        Store(
            id=uuid.uuid4(),
            code=f"SS-{site.code}"[:30],
            name=f"{site.name} Site Store",
            type=StoreType.SITE,
            site_id=site.id,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
    )


_crud("categories", MaterialCategory, CategoryIn, CategoryUpdate, CategoryOut, label="Category", query_model=CategoryQuery)
_crud("uoms", Uom, UomIn, UomUpdate, UomOut, label="UOM")
_crud(
    "uom-conversions",
    UomConversion,
    ConversionIn,
    ConversionUpdate,
    ConversionOut,
    label="UOM conversion",
    search=(),
    query_model=ConversionQuery,
)
_crud(
    "materials",
    Material,
    MaterialIn,
    MaterialUpdate,
    MaterialOut,
    label="Material",
    search=("code", "name", "hsn_code"),
    query_model=MaterialQuery,
)
_crud("vendors", Vendor, VendorIn, VendorUpdate, VendorOut, manage_perm="vendors.manage", label="Vendor", search=("code", "name", "gstin"))
_crud("projects", Project, ProjectIn, ProjectUpdate, ProjectOut, label="Project", search=("code", "name", "city"))
_crud(
    "sites",
    Site,
    SiteIn,
    SiteUpdate,
    SiteOut,
    label="Site",
    query_model=SiteQuery,
    to_out=_site_out,
    after_create=_create_site_store,
)
_crud(
    "stores",
    Store,
    StoreIn,
    StoreUpdate,
    StoreOut,
    label="Store",
    query_model=StoreQuery,
    create_defaults={"type": StoreType.CENTRAL},
)


# ------------------------------------------------------------------ CSV import


class ImportResult(BaseModel):
    created: int
    updated: int


_MAX_IMPORT_BYTES = 2 * 1024 * 1024


async def _read_csv(file: UploadFile) -> list[dict[str, str]]:
    raw = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(raw) > _MAX_IMPORT_BYTES:
        raise AppError("PAYLOAD_TOO_LARGE", "CSV must be under 2 MB", 413)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AppError("VALIDATION_ERROR", "CSV must be UTF-8 encoded", 422)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise AppError("VALIDATION_ERROR", "CSV has no data rows", 422)
    if len(rows) > 5000:
        raise AppError("VALIDATION_ERROR", "Import at most 5000 rows at a time", 422)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]


@router.post(
    "/materials/import",
    response_model=Envelope[ImportResult],
    summary="CSV: code,name,category_code,uom_code,hsn_code,gst_rate,reorder_level,specification",
)
async def import_materials(db: DB, ctx: Perm("masters.manage"), file: UploadFile = File(...)):
    rows = await _read_csv(file)
    cats = {c.code.upper(): c.id for c in (await db.execute(select(MaterialCategory))).scalars()}
    uoms = {u.code.upper(): u.id for u in (await db.execute(select(Uom))).scalars()}
    errors, parsed = [], []
    for i, r in enumerate(rows, start=2):
        try:
            cat = cats.get(r.get("category_code", "").upper())
            uom = uoms.get(r.get("uom_code", "").upper())
            if not cat:
                raise ValueError(f"unknown category_code '{r.get('category_code')}'")
            if not uom:
                raise ValueError(f"unknown uom_code '{r.get('uom_code')}'")
            parsed.append(
                MaterialIn(
                    code=r.get("code", ""),
                    name=r.get("name", ""),
                    category_id=cat,
                    base_uom_id=uom,
                    hsn_code=r.get("hsn_code") or None,
                    gst_rate=Decimal(r.get("gst_rate") or "18"),
                    reorder_level=Decimal(r.get("reorder_level") or "0"),
                    specification=r.get("specification") or None,
                )
            )
        except (ValidationError, ValueError, ArithmeticError) as exc:
            errors.append({"row": i, "message": str(exc).splitlines()[0][:300]})
    if errors:
        raise AppError("IMPORT_FAILED", f"{len(errors)} row(s) have errors; nothing was imported", 422, errors[:200])
    created = updated = 0
    existing = {
        m.code: m for m in (await db.execute(select(Material).where(Material.code.in_([p.code for p in parsed])))).unique().scalars()
    }
    for p in parsed:
        if p.code in existing:
            m = existing[p.code]
            for k, v in p.model_dump(exclude={"code", "base_uom_id"}).items():
                setattr(m, k, v)
            bump(m, ctx)
            updated += 1
        else:
            db.add(Material(id=uuid.uuid4(), **p.model_dump(), created_by_id=ctx.user_id, updated_by_id=ctx.user_id))
            created += 1
    await audit(db, ctx, "IMPORT", "materials", None, f"CSV import: {created} created, {updated} updated")
    await db.commit()
    return respond(ctx, ImportResult(created=created, updated=updated))


@router.post(
    "/vendors/import", response_model=Envelope[ImportResult], summary="CSV: code,name,gstin,pan,contact_person,phone,email,address"
)
async def import_vendors(db: DB, ctx: Perm("vendors.manage"), file: UploadFile = File(...)):
    rows = await _read_csv(file)
    errors, parsed = [], []
    for i, r in enumerate(rows, start=2):
        try:
            parsed.append(VendorIn(**{k: (r.get(k) or None) for k in VendorIn.model_fields if k in r or k in ("code", "name")}))
        except ValidationError as exc:
            errors.append({"row": i, "message": str(exc).splitlines()[0][:300] + ": " + exc.errors()[0]["msg"]})
    if errors:
        raise AppError("IMPORT_FAILED", f"{len(errors)} row(s) have errors; nothing was imported", 422, errors[:200])
    created = updated = 0
    existing = {v.code: v for v in (await db.execute(select(Vendor).where(Vendor.code.in_([p.code for p in parsed])))).scalars()}
    for p in parsed:
        if p.code in existing:
            v = existing[p.code]
            for k, val in p.model_dump(exclude={"code"}).items():
                setattr(v, k, val)
            bump(v, ctx)
            updated += 1
        else:
            db.add(Vendor(id=uuid.uuid4(), **p.model_dump(), created_by_id=ctx.user_id, updated_by_id=ctx.user_id))
            created += 1
    await audit(db, ctx, "IMPORT", "vendors", None, f"CSV import: {created} created, {updated} updated")
    await db.commit()
    return respond(ctx, ImportResult(created=created, updated=updated))
