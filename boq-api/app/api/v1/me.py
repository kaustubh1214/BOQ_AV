import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import DB, Ctx, get_ctx_allow_password_change, respond
from app.core.config import settings
from app.core.responses import Envelope, Message
from app.models import DeviceToken, Project, Site, Store
from app.models.enums import Platform
from app.schemas.common import In, Out
from app.services.common import financial_year, today
from app.services.context import ScopeSet, UserContext

router = APIRouter(tags=["me"])


class ScopeItem(Out):
    id: uuid.UUID
    code: str
    name: str
    project_id: uuid.UUID | None = None
    type: str | None = None


class ScopesOut(Out):
    all: bool
    projects: list[ScopeItem]
    sites: list[ScopeItem]
    stores: list[ScopeItem]


class MeOut(Out):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None
    must_change_password: bool
    roles: list[str]
    permissions: list[str]
    show_finance: bool
    scopes: ScopesOut
    company_name: str
    financial_year: str
    today: str


class DeviceTokenIn(In):
    token: Annotated[str, Field(min_length=10, max_length=300)]
    platform: Platform


async def _scopes(db, ctx: UserContext) -> ScopesOut:
    merged = ScopeSet()
    for g in ctx.grants:
        merged.merge(g.scope)
    if merged.all:
        return ScopesOut(all=True, projects=[], sites=[], stores=[])
    projects = (await db.execute(select(Project).where(Project.id.in_(merged.project_ids)))).scalars().all()
    sites = (await db.execute(select(Site).where(Site.id.in_(merged.site_ids)))).scalars().all()
    stores = (await db.execute(select(Store).where(Store.id.in_(merged.store_ids)))).scalars().all()
    # A site-scoped user still needs to know which project their site belongs to.
    extra_project_ids = {s.project_id for s in sites} - {p.id for p in projects}
    if extra_project_ids:
        projects = [*projects, *(await db.execute(select(Project).where(Project.id.in_(extra_project_ids)))).scalars().all()]
    return ScopesOut(
        all=False,
        projects=[ScopeItem(id=p.id, code=p.code, name=p.name) for p in projects],
        sites=[ScopeItem(id=s.id, code=s.code, name=s.name, project_id=s.project_id) for s in sites],
        stores=[ScopeItem(id=s.id, code=s.code, name=s.name, type=s.type) for s in stores],
    )


@router.get("/me", response_model=Envelope[MeOut])
async def me(db: DB, ctx: Annotated[UserContext, Depends(get_ctx_allow_password_change)]):
    u = ctx.user
    data = MeOut(
        id=u.id,
        name=u.name,
        email=u.email,
        phone=u.phone,
        must_change_password=u.must_change_password,
        roles=ctx.role_codes,
        permissions=sorted(ctx.permissions),
        show_finance=ctx.show_finance,
        scopes=await _scopes(db, ctx),
        company_name=settings.COMPANY_NAME,
        financial_year=financial_year(),
        today=today().isoformat(),
    )
    return {"success": True, "data": data, "meta": {}}


@router.post("/me/device-tokens", response_model=Envelope[Message], summary="Register an FCM token (Flutter)")
async def register_device(body: DeviceTokenIn, db: DB, ctx: Ctx):
    await db.execute(
        pg_insert(DeviceToken)
        .values(id=uuid.uuid4(), user_id=ctx.user_id, token=body.token, platform=body.platform)
        .on_conflict_do_update(index_elements=["token"], set_={"user_id": ctx.user_id, "platform": body.platform})
    )
    await db.commit()
    return respond(ctx, {"message": "registered"})
