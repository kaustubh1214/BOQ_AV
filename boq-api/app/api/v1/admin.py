import uuid
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import EmailStr, Field
from sqlalchemy import delete, func, or_, select

from app.api.deps import DB, PagingDep, Perm, get_or_404, respond, respond_page
from app.core.errors import AppError, forbidden
from app.core.responses import CursorEnvelope, Envelope, PageEnvelope
from app.core.security import hash_password, validate_password_strength
from app.models import AuditLog, Permission, Project, Role, RolePermission, Site, Store, User, UserRoleAssignment
from app.models.enums import ScopeType
from app.schemas.common import In, Out
from app.services.common import audit, snapshot
from app.services.context import UserContext

router = APIRouter(prefix="/admin", tags=["admin"])


# ------------------------------------------------------------------ schemas


class AssignmentIn(In):
    role_id: uuid.UUID
    scope_type: ScopeType
    scope_id: uuid.UUID | None = None


class AssignmentOut(Out):
    id: uuid.UUID
    role_id: uuid.UUID
    role_code: str
    role_name: str
    scope_type: str
    scope_id: uuid.UUID | None
    scope_name: str | None = None


class UserCreateIn(In):
    name: Annotated[str, Field(min_length=2, max_length=120)]
    email: EmailStr
    phone: Annotated[str | None, Field(max_length=20)] = None
    password: Annotated[str, Field(max_length=200)]
    assignments: list[AssignmentIn] = []


class UserUpdateIn(In):
    name: Annotated[str | None, Field(min_length=2, max_length=120)] = None
    phone: Annotated[str | None, Field(max_length=20)] = None
    is_active: bool | None = None


class ResetPasswordIn(In):
    password: Annotated[str, Field(max_length=200)]


class UserOut(Out):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    locked_until: datetime | None
    assignments: list[AssignmentOut] = []


class RoleIn(In):
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,39}$")]
    name: Annotated[str, Field(min_length=2, max_length=80)]
    description: Annotated[str | None, Field(max_length=300)] = None
    default_scope: ScopeType = ScopeType.GLOBAL
    permissions: list[str] = []


class RolePermissionsIn(In):
    permissions: list[str]


class RoleOut(Out):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    default_scope: str
    permissions: list[str]
    user_count: int = 0


class PermissionOut(Out):
    code: str
    module: str
    description: str


class AuditOut(Out):
    id: int
    user_id: uuid.UUID | None
    user_name: str | None = None
    action: str
    entity_type: str
    entity_id: str | None
    summary: str | None
    before: dict | None
    after: dict | None
    ip: str | None
    request_id: str | None
    created_at: datetime


# ------------------------------------------------------------------ helpers


async def _scope_names(db, assignments: list[UserRoleAssignment]) -> dict[uuid.UUID, str]:
    ids = {a.scope_id for a in assignments if a.scope_id}
    if not ids:
        return {}
    out: dict[uuid.UUID, str] = {}
    for model in (Project, Site, Store):
        for id_, name in await db.execute(select(model.id, model.name).where(model.id.in_(ids))):
            out[id_] = name
    return out


async def _user_out(db, user: User) -> UserOut:
    names = await _scope_names(db, user.assignments)
    out = UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        phone=user.phone,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        locked_until=user.locked_until,
    )
    out.assignments = [
        AssignmentOut(
            id=a.id,
            role_id=a.role_id,
            role_code=a.role.code,
            role_name=a.role.name,
            scope_type=a.scope_type,
            scope_id=a.scope_id,
            scope_name=names.get(a.scope_id) if a.scope_id else "All",
        )
        for a in user.assignments
    ]
    return out


async def _validate_assignments(db, ctx: UserContext, items: list[AssignmentIn]) -> list[Role]:
    roles = []
    for a in items:
        role = await db.get(Role, a.role_id)
        if role is None:
            raise AppError("INVALID_REFERENCE", "Role not found", 422)
        # No privilege escalation: you can only hand out permissions you hold yourself.
        missing = {rp.permission_code for rp in role.permissions} - ctx.permissions
        if missing:
            raise forbidden(f"You cannot assign role {role.name}: it grants permissions you don't have")
        if a.scope_type == ScopeType.GLOBAL:
            if a.scope_id is not None:
                raise AppError("VALIDATION_ERROR", "GLOBAL scope must not have a scope_id", 422)
        else:
            model = {ScopeType.PROJECT: Project, ScopeType.SITE: Site, ScopeType.STORE: Store}[a.scope_type]
            if a.scope_id is None or await db.get(model, a.scope_id) is None:
                raise AppError("INVALID_REFERENCE", f"{a.scope_type.title()} for assignment not found", 422)
        roles.append(role)
    return roles


async def _load_user(db, user_id: uuid.UUID) -> User:
    return await get_or_404(db, User, user_id, "User")


# ------------------------------------------------------------------ users


@router.get("/users", response_model=PageEnvelope[UserOut])
async def list_users(db: DB, ctx: Perm("users.manage"), paging: PagingDep, q: str | None = None, active: bool | None = None):
    stmt = select(User)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.name.ilike(like), User.email.ilike(like)))
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    users = (await db.execute(stmt.order_by(User.name).offset(paging.offset).limit(paging.limit))).scalars().all()
    return respond_page(ctx, [await _user_out(db, u) for u in users], total, paging)


@router.post("/users", response_model=Envelope[UserOut], status_code=201)
async def create_user(body: UserCreateIn, db: DB, ctx: Perm("users.manage")):
    if err := validate_password_strength(body.password):
        raise AppError("WEAK_PASSWORD", err, 422)
    email = body.email.lower()
    if (await db.execute(select(User.id).where(func.lower(User.email) == email))).first():
        raise AppError("DUPLICATE", "A user with this email already exists", 409)
    await _validate_assignments(db, ctx, body.assignments)
    user = User(
        id=uuid.uuid4(),
        name=body.name,
        email=email,
        phone=body.phone,
        password_hash=hash_password(body.password),
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    for a in body.assignments:
        db.add(UserRoleAssignment(user_id=user.id, role_id=a.role_id, scope_type=a.scope_type, scope_id=a.scope_id))
    await audit(db, ctx, "CREATE", "user", user.id, f"Created user {email}", None, snapshot(user))
    await db.commit()
    db.expunge_all()
    return respond(ctx, await _user_out(db, await _load_user(db, user.id)))


@router.get("/users/{user_id}", response_model=Envelope[UserOut])
async def get_user(user_id: uuid.UUID, db: DB, ctx: Perm("users.manage")):
    return respond(ctx, await _user_out(db, await _load_user(db, user_id)))


@router.patch("/users/{user_id}", response_model=Envelope[UserOut])
async def update_user(user_id: uuid.UUID, body: UserUpdateIn, db: DB, ctx: Perm("users.manage")):
    user = await _load_user(db, user_id)
    if user.id == ctx.user_id and body.is_active is False:
        raise AppError("VALIDATION_ERROR", "You cannot deactivate yourself", 422)
    before = snapshot(user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    if body.is_active is False:
        user.token_version += 1  # kick active sessions immediately
    await audit(db, ctx, "UPDATE", "user", user.id, None, before, snapshot(user))
    await db.commit()
    return respond(ctx, await _user_out(db, user))


@router.post("/users/{user_id}/reset-password", response_model=Envelope[UserOut])
async def reset_password(user_id: uuid.UUID, body: ResetPasswordIn, db: DB, ctx: Perm("users.manage")):
    if err := validate_password_strength(body.password):
        raise AppError("WEAK_PASSWORD", err, 422)
    user = await _load_user(db, user_id)
    user.password_hash = hash_password(body.password)
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None
    user.token_version += 1
    await audit(db, ctx, "PASSWORD_RESET", "user", user.id, "Password reset by admin")
    await db.commit()
    return respond(ctx, await _user_out(db, user))


@router.put("/users/{user_id}/assignments", response_model=Envelope[UserOut])
async def set_assignments(user_id: uuid.UUID, body: list[AssignmentIn], db: DB, ctx: Perm("users.manage")):
    if user_id == ctx.user_id:
        raise forbidden("You cannot change your own role assignments")
    user = await _load_user(db, user_id)
    await _validate_assignments(db, ctx, body)
    before = {"assignments": [f"{a.role.code}:{a.scope_type}:{a.scope_id}" for a in user.assignments]}
    await db.execute(delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id))
    for a in body:
        db.add(UserRoleAssignment(user_id=user.id, role_id=a.role_id, scope_type=a.scope_type, scope_id=a.scope_id))
    user.token_version += 1  # new permissions take effect on next login/refresh
    await db.flush()
    db.expire(user)
    user = await _load_user(db, user_id)
    after = {"assignments": [f"{a.role.code}:{a.scope_type}:{a.scope_id}" for a in user.assignments]}
    await audit(db, ctx, "ASSIGN_ROLES", "user", user.id, None, before, after)
    await db.commit()
    return respond(ctx, await _user_out(db, user))


# ------------------------------------------------------------------ roles & permissions


async def _role_out(db, role: Role) -> RoleOut:
    count = (
        await db.execute(select(func.count(func.distinct(UserRoleAssignment.user_id))).where(UserRoleAssignment.role_id == role.id))
    ).scalar_one()
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        default_scope=role.default_scope,
        permissions=sorted(rp.permission_code for rp in role.permissions),
        user_count=count,
    )


@router.get("/permissions", response_model=Envelope[list[PermissionOut]])
async def list_permissions(db: DB, ctx: Perm("roles.manage", "users.manage")):
    rows = (await db.execute(select(Permission).order_by(Permission.module, Permission.code))).scalars().all()
    return respond(ctx, [PermissionOut.model_validate(p) for p in rows])


@router.get("/roles", response_model=Envelope[list[RoleOut]])
async def list_roles(db: DB, ctx: Perm("roles.manage", "users.manage")):
    roles = (await db.execute(select(Role).order_by(Role.is_system.desc(), Role.name))).scalars().all()
    return respond(ctx, [await _role_out(db, r) for r in roles])


async def _check_perm_codes(db, ctx: UserContext, codes: list[str]) -> None:
    known = set((await db.execute(select(Permission.code))).scalars())
    unknown = set(codes) - known
    if unknown:
        raise AppError("VALIDATION_ERROR", f"Unknown permissions: {', '.join(sorted(unknown))}", 422)
    if set(codes) - ctx.permissions:
        raise forbidden("You cannot grant permissions you don't have")


@router.post("/roles", response_model=Envelope[RoleOut], status_code=201)
async def create_role(body: RoleIn, db: DB, ctx: Perm("roles.manage")):
    await _check_perm_codes(db, ctx, body.permissions)
    role = Role(id=uuid.uuid4(), code=body.code, name=body.name, description=body.description, default_scope=body.default_scope)
    db.add(role)
    await db.flush()
    for code in set(body.permissions):
        db.add(RolePermission(role_id=role.id, permission_code=code))
    await audit(db, ctx, "CREATE", "role", role.id, f"Created role {role.code}", None, {"permissions": sorted(body.permissions)})
    await db.commit()
    db.expunge_all()
    return respond(ctx, await _role_out(db, await get_or_404(db, Role, role.id)))


@router.put("/roles/{role_id}/permissions", response_model=Envelope[RoleOut])
async def set_role_permissions(role_id: uuid.UUID, body: RolePermissionsIn, db: DB, ctx: Perm("roles.manage")):
    role = await get_or_404(db, Role, role_id, "Role")
    if role.is_system:
        raise AppError("SYSTEM_ROLE", "System roles are fixed. Create a custom role (you can copy this one).", 409)
    await _check_perm_codes(db, ctx, body.permissions)
    before = sorted(rp.permission_code for rp in role.permissions)
    await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    for code in set(body.permissions):
        db.add(RolePermission(role_id=role.id, permission_code=code))
    # Everyone holding this role must re-authenticate to pick up the change.
    holders = select(UserRoleAssignment.user_id).where(UserRoleAssignment.role_id == role.id)
    for u in (await db.execute(select(User).where(User.id.in_(holders)))).scalars():
        u.token_version += 1
    await audit(
        db,
        ctx,
        "UPDATE",
        "role",
        role.id,
        f"Permissions of {role.code}",
        {"permissions": before},
        {"permissions": sorted(body.permissions)},
    )
    await db.commit()
    db.expunge_all()
    return respond(ctx, await _role_out(db, await get_or_404(db, Role, role.id)))


# ------------------------------------------------------------------ audit trail


@router.get("/audit-logs", response_model=CursorEnvelope[AuditOut])
async def audit_logs(
    db: DB,
    ctx: Perm("audit.read"),
    entity_type: str | None = None,
    entity_id: str | None = None,
    user_id: uuid.UUID | None = None,
    action: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: Annotated[int | None, Query(description="id of the last row from the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    stmt = select(AuditLog, User.name).outerjoin(User, User.id == AuditLog.user_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        stmt = stmt.where(AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    if cursor:
        stmt = stmt.where(AuditLog.id < cursor)
    rows = (await db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit))).all()
    data = []
    for log, uname in rows:
        o = AuditOut.model_validate(log)
        o.user_name = uname
        data.append(o)
    next_cursor = data[-1].id if len(data) == limit else None
    return {"success": True, "data": data, "meta": {"limit": limit, "nextCursor": next_cursor}}
