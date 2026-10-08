"""In-app notifications. Recipients are resolved from the permission matrix + scopes, so the right
Project Manager / Store Manager / Site Engineer is alerted without hard-coding users."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, RolePermission, Site, Store, User, UserRoleAssignment
from app.models.enums import ScopeType


async def recipients(
    db: AsyncSession,
    perm: str,
    *,
    project_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
) -> set[uuid.UUID]:
    rows = await db.execute(
        select(UserRoleAssignment.user_id, UserRoleAssignment.scope_type, UserRoleAssignment.scope_id)
        .join(RolePermission, RolePermission.role_id == UserRoleAssignment.role_id)
        .join(User, User.id == UserRoleAssignment.user_id)
        .where(RolePermission.permission_code == perm, User.is_active.is_(True))
    )
    site_store = None
    if site_id is not None:
        site_store = (await db.execute(select(Store.id).where(Store.site_id == site_id))).scalar_one_or_none()
        if project_id is None:
            project_id = (await db.execute(select(Site.project_id).where(Site.id == site_id))).scalar_one_or_none()
    out: set[uuid.UUID] = set()
    for user_id, scope_type, scope_id in rows:
        if (
            scope_type == ScopeType.GLOBAL
            or (scope_type == ScopeType.PROJECT and scope_id == project_id)
            or (scope_type == ScopeType.SITE and scope_id == site_id)
            or (scope_type == ScopeType.STORE and scope_id in (store_id, site_store))
        ):
            out.add(user_id)
    return out


async def notify(
    db: AsyncSession,
    perm: str,
    *,
    type: str,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    exclude: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    also: set[uuid.UUID] | None = None,
) -> None:
    users = await recipients(db, perm, project_id=project_id, site_id=site_id, store_id=store_id)
    users |= also or set()
    users.discard(exclude)  # type: ignore[arg-type]
    for uid in users:
        db.add(
            Notification(
                user_id=uid, type=type, title=title[:200], body=(body or "")[:500] or None, entity_type=entity_type, entity_id=entity_id
            )
        )


def notify_users(
    db: AsyncSession,
    users: set[uuid.UUID | None],
    *,
    type: str,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    exclude: uuid.UUID | None = None,
) -> None:
    for uid in users:
        if uid and uid != exclude:
            db.add(
                Notification(
                    user_id=uid, type=type, title=title[:200], body=(body or "")[:500] or None, entity_type=entity_type, entity_id=entity_id
                )
            )
