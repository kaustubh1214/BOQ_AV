import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Material, Site, Store
from app.models.enums import StoreType


async def central_store(db: AsyncSession) -> Store:
    store = (
        (await db.execute(select(Store).where(Store.type == StoreType.CENTRAL, Store.is_active.is_(True)).order_by(Store.created_at)))
        .scalars()
        .first()
    )
    if store is None:
        raise AppError("NO_CENTRAL_STORE", "No active central store configured", 409)
    return store


async def site_store(db: AsyncSession, site_id: uuid.UUID) -> Store:
    store = (await db.execute(select(Store).where(Store.site_id == site_id))).scalar_one_or_none()
    if store is None:
        raise AppError("NO_SITE_STORE", "Site has no store", 409)
    return store


async def get_site(db: AsyncSession, site_id: uuid.UUID) -> Site:
    site = await db.get(Site, site_id)
    if site is None or not site.is_active:
        raise AppError("INVALID_REFERENCE", "Site not found or inactive", 422)
    return site


async def materials_by_id(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, Material]:
    rows = (await db.execute(select(Material).where(Material.id.in_(set(ids))))).unique().scalars().all()
    found = {m.id: m for m in rows}
    missing = set(ids) - set(found)
    if missing:
        raise AppError("INVALID_REFERENCE", "One or more materials not found", 422, {"material_ids": [str(m) for m in missing]})
    inactive = [m.name for m in rows if not m.is_active]
    if inactive:
        raise AppError("INACTIVE_MATERIAL", f"Inactive materials: {', '.join(inactive)}", 422)
    return found


def ensure_unique_materials(items) -> None:
    seen = set()
    for it in items:
        if it.material_id in seen:
            raise AppError("DUPLICATE_LINE", "Each material may appear only once per document", 422)
        seen.add(it.material_id)
