"""Who is calling and what they may touch.

A user can hold several role assignments, each with its own scope (GLOBAL / PROJECT / SITE / STORE).
A permission is only effective inside the scope of the assignment that grants it, so a user who is
Project Manager on project A and Site Engineer on site B cannot approve requests on site B.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden
from app.models import Site, Store, User
from app.models.enums import ScopeType


@dataclass
class ScopeSet:
    all: bool = False
    project_ids: set[uuid.UUID] = field(default_factory=set)
    site_ids: set[uuid.UUID] = field(default_factory=set)
    store_ids: set[uuid.UUID] = field(default_factory=set)

    def merge(self, other: "ScopeSet") -> None:
        self.all = self.all or other.all
        self.project_ids |= other.project_ids
        self.site_ids |= other.site_ids
        self.store_ids |= other.store_ids

    def covers(
        self,
        *,
        project_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        store_ids: tuple[uuid.UUID | None, ...] = (),
    ) -> bool:
        if self.all:
            return True
        if project_id is not None and project_id in self.project_ids:
            return True
        if site_id is not None and site_id in self.site_ids:
            return True
        return any(s is not None and s in self.store_ids for s in store_ids)

    @property
    def empty(self) -> bool:
        return not (self.all or self.project_ids or self.site_ids or self.store_ids)


@dataclass
class Grant:
    role_code: str
    permissions: frozenset[str]
    scope_type: str
    scope_id: uuid.UUID | None
    scope: ScopeSet


@dataclass
class UserContext:
    user: User
    grants: list[Grant]
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def permissions(self) -> set[str]:
        out: set[str] = set()
        for g in self.grants:
            out |= g.permissions
        return out

    def has(self, perm: str) -> bool:
        return any(perm in g.permissions for g in self.grants)

    def require(self, perm: str) -> None:
        if not self.has(perm):
            raise forbidden(f"Missing permission '{perm}'")

    def scope(self, perm: str) -> ScopeSet:
        out = ScopeSet()
        for g in self.grants:
            if perm in g.permissions:
                out.merge(g.scope)
        return out

    def covers(self, perm: str, **kw) -> bool:
        return self.scope(perm).covers(**kw)

    @property
    def show_finance(self) -> bool:
        return self.has("finance.view")

    @property
    def role_codes(self) -> list[str]:
        return sorted({g.role_code for g in self.grants})


async def load_context(db: AsyncSession, user: User, **request_info) -> UserContext:
    assignments = user.assignments
    project_scopes = {a.scope_id for a in assignments if a.scope_type == ScopeType.PROJECT and a.scope_id}
    site_scopes = {a.scope_id for a in assignments if a.scope_type == ScopeType.SITE and a.scope_id}

    sites_by_project: dict[uuid.UUID, set[uuid.UUID]] = {}
    if project_scopes:
        rows = await db.execute(select(Site.id, Site.project_id).where(Site.project_id.in_(project_scopes)))
        for sid, pid in rows:
            sites_by_project.setdefault(pid, set()).add(sid)

    all_scoped_sites = site_scopes | {s for ss in sites_by_project.values() for s in ss}
    store_by_site: dict[uuid.UUID, uuid.UUID] = {}
    if all_scoped_sites:
        rows = await db.execute(select(Store.site_id, Store.id).where(Store.site_id.in_(all_scoped_sites)))
        store_by_site = {site_id: store_id for site_id, store_id in rows}

    grants: list[Grant] = []
    for a in assignments:
        perms = frozenset(rp.permission_code for rp in a.role.permissions)
        scope = ScopeSet()
        if a.scope_type == ScopeType.GLOBAL:
            scope.all = True
        elif a.scope_type == ScopeType.PROJECT and a.scope_id:
            sites = sites_by_project.get(a.scope_id, set())
            scope.project_ids = {a.scope_id}
            scope.site_ids = set(sites)
            scope.store_ids = {store_by_site[s] for s in sites if s in store_by_site}
        elif a.scope_type == ScopeType.SITE and a.scope_id:
            scope.site_ids = {a.scope_id}
            if a.scope_id in store_by_site:
                scope.store_ids = {store_by_site[a.scope_id]}
        elif a.scope_type == ScopeType.STORE and a.scope_id:
            scope.store_ids = {a.scope_id}
        grants.append(Grant(a.role.code, perms, a.scope_type, a.scope_id, scope))

    return UserContext(user=user, grants=grants, **request_info)


def scope_filter(scope: ScopeSet, *, store_cols: tuple = (), site_col=None, project_col=None):
    """SQL predicate restricting a document query to what `scope` covers."""
    from sqlalchemy import false, or_, true

    if scope.all:
        return true()
    conds = [c.in_(scope.store_ids) for c in store_cols if scope.store_ids]
    if site_col is not None and scope.site_ids:
        conds.append(site_col.in_(scope.site_ids))
    if project_col is not None and scope.project_ids:
        conds.append(project_col.in_(scope.project_ids))
    return or_(*conds) if conds else false()


def ensure_covers(ctx: UserContext, perm: str, label: str = "Record", **kw) -> None:
    """Out-of-scope records answer 404 (not 403) so IDs from other sites can't be probed."""
    from app.core.errors import AppError

    if not ctx.covers(perm, **kw):
        if ctx.has(perm):
            raise AppError("NOT_FOUND", f"{label} not found", 404)
        raise AppError("FORBIDDEN", f"Missing permission '{perm}'", 403)
