import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def user_names(db: AsyncSession, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    return {uid: name for uid, name in await db.execute(select(User.id, User.name).where(User.id.in_(wanted)))}
