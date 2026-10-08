import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Decimals travel as JSON numbers (easy for React/Flutter); all maths server-side stays Decimal.
Num = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
OptNum = Annotated[Decimal | None, PlainSerializer(lambda v: None if v is None else float(v), return_type=float | None, when_used="json")]

PosQty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]
NonNegQty = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)]
Rate = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Ref(Out):
    id: uuid.UUID
    code: str | None = None
    name: str


class UserRef(Out):
    id: uuid.UUID
    name: str


class ApprovalOut(Out):
    stage: str
    action: str
    actor_id: uuid.UUID
    actor_name: str | None = None
    comment: str | None = None
    acted_at: datetime


class CommentIn(In):
    comment: Annotated[str | None, Field(max_length=1000)] = None
    version: int | None = None


class CancelIn(In):
    reason: Annotated[str, Field(min_length=3, max_length=1000)]
    version: int | None = None


class VersionIn(In):
    version: int | None = None


class DateRange(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
