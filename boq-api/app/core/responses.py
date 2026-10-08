"""Response envelope shared by every endpoint: {success, data, meta}. Errors: {success: false, error}."""

import math
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PageMeta(BaseModel):
    page: int
    limit: int
    total: int
    totalPages: int


class CursorMeta(BaseModel):
    limit: int
    nextCursor: int | None = None


class Envelope(BaseModel, Generic[T]):
    success: bool = True
    data: T
    meta: dict[str, Any] = {}


class PageEnvelope(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    meta: PageMeta


class CursorEnvelope(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    meta: CursorMeta


class Message(BaseModel):
    message: str


def ok(data: Any, meta: dict | None = None) -> dict:
    return {"success": True, "data": data, "meta": meta or {}}


def page(data: list, total: int, page_no: int, limit: int) -> dict:
    return {
        "success": True,
        "data": data,
        "meta": {"page": page_no, "limit": limit, "total": total, "totalPages": max(1, math.ceil(total / limit))},
    }
