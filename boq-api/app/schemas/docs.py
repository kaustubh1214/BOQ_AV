"""Shared bits of document schemas."""

import uuid
from datetime import date, datetime

from app.models import Material
from app.schemas.common import Out


class MaterialBrief(Out):
    id: uuid.UUID
    code: str
    name: str
    uom: str


def mat_brief(m: Material) -> MaterialBrief:
    return MaterialBrief(id=m.id, code=m.code, name=m.name, uom=m.base_uom.code)


class DocHeader(Out):
    """Fields every transactional document exposes."""

    id: uuid.UUID
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    created_by_id: uuid.UUID | None
    created_by_name: str | None = None


class StoreBrief(Out):
    id: uuid.UUID
    code: str
    name: str


class SiteBrief(Out):
    id: uuid.UUID
    code: str
    name: str
    project_id: uuid.UUID
    project_name: str | None = None


__all__ = ["DocHeader", "MaterialBrief", "SiteBrief", "StoreBrief", "date", "mat_brief"]
