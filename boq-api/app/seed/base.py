"""Idempotent base seed: permission catalog, system roles, UOMs, global conversions, central store, admin."""

import logging
import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, validate_password_strength
from app.models import Permission, Role, RolePermission, Store, Uom, UomConversion, User, UserRoleAssignment
from app.models.enums import ScopeType, StoreType
from app.services.permissions import PERMISSIONS, ROLES

log = logging.getLogger("boq.seed")

UOMS = {
    "KG": "Kilogram",
    "MT": "Metric tonne",
    "BAG": "Bag",
    "NOS": "Numbers",
    "CUM": "Cubic metre",
    "CFT": "Cubic feet",
    "SQM": "Square metre",
    "SQFT": "Square feet",
    "RMT": "Running metre",
    "LTR": "Litre",
    "BOX": "Box",
    "BDL": "Bundle",
    "COIL": "Coil",
}

GLOBAL_CONVERSIONS = [("MT", "KG", Decimal("1000")), ("CUM", "CFT", Decimal("35.3147"))]


async def seed_base(db: AsyncSession) -> dict:
    # Permissions
    existing = {p.code: p for p in (await db.execute(select(Permission))).scalars()}
    for code, (module, desc) in PERMISSIONS.items():
        if code in existing:
            existing[code].module, existing[code].description = module, desc
        else:
            db.add(Permission(code=code, module=module, description=desc))
    await db.flush()

    # System roles (permissions re-synced every run so code is the source of truth)
    roles = {r.code: r for r in (await db.execute(select(Role))).scalars()}
    for code, (name, scope, desc, perms) in ROLES.items():
        role = roles.get(code)
        if role is None:
            role = Role(id=uuid.uuid4(), code=code, name=name, description=desc, default_scope=scope, is_system=True)
            db.add(role)
            roles[code] = role
        else:
            role.name, role.description, role.default_scope, role.is_system = name, desc, scope, True
        await db.flush()
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        for p in sorted(set(perms)):
            db.add(RolePermission(role_id=role.id, permission_code=p))
    await db.flush()

    # UOMs + global conversions
    uoms = {u.code: u for u in (await db.execute(select(Uom))).scalars()}
    for code, name in UOMS.items():
        if code not in uoms:
            uoms[code] = Uom(id=uuid.uuid4(), code=code, name=name)
            db.add(uoms[code])
    await db.flush()
    for frm, to, factor in GLOBAL_CONVERSIONS:
        exists = (
            await db.execute(
                select(UomConversion.id).where(
                    UomConversion.material_id.is_(None), UomConversion.from_uom_id == uoms[frm].id, UomConversion.to_uom_id == uoms[to].id
                )
            )
        ).first()
        if not exists:
            db.add(UomConversion(id=uuid.uuid4(), from_uom_id=uoms[frm].id, to_uom_id=uoms[to].id, factor=factor))

    # Central store
    central = (await db.execute(select(Store).where(Store.type == StoreType.CENTRAL))).scalars().first()
    if central is None:
        central = Store(id=uuid.uuid4(), code="CS-01", name=f"{settings.COMPANY_NAME} Central Store", type=StoreType.CENTRAL)
        db.add(central)
    await db.flush()

    # Admin
    admin = None
    if settings.SEED_ADMIN_EMAIL and settings.SEED_ADMIN_PASSWORD:
        email = settings.SEED_ADMIN_EMAIL.lower()
        admin = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
        if admin is None:
            if err := validate_password_strength(settings.SEED_ADMIN_PASSWORD):
                raise SystemExit(f"SEED_ADMIN_PASSWORD rejected: {err}")
            admin = User(
                id=uuid.uuid4(), name="System Administrator", email=email, password_hash=hash_password(settings.SEED_ADMIN_PASSWORD)
            )
            db.add(admin)
            await db.flush()
            db.add(UserRoleAssignment(user_id=admin.id, role_id=roles["SUPER_ADMIN"].id, scope_type=ScopeType.GLOBAL))
            log.info("created admin %s", email)
    else:
        log.warning("SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD not set — no admin user created")

    await db.commit()
    return {"roles": {c: r.id for c, r in roles.items()}, "uoms": {c: u.id for c, u in uoms.items()}, "central_store_id": central.id}
