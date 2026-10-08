from enum import StrEnum

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import Ctx, respond
from app.core.config import settings
from app.core.responses import Envelope
from app.models import enums
from app.services.common import financial_year, today

router = APIRouter(prefix="/meta", tags=["meta"])

_EXPOSED = [
    enums.ProjectStatus,
    enums.StoreType,
    enums.RateSource,
    enums.BoqStatus,
    enums.GrnStatus,
    enums.RfqStatus,
    enums.Priority,
    enums.IssueStatus,
    enums.ReturnStatus,
    enums.EntryStatus,
    enums.AdjustmentReason,
    enums.AdjustmentStatus,
    enums.TxnType,
    enums.ScopeType,
]


class EnumValue(BaseModel):
    value: str
    label: str


class CompanyOut(BaseModel):
    name: str
    financial_year: str
    today: str
    timezone: str
    grn_tolerance_pct: float
    maker_checker_on_posting: bool


def _label(v: str) -> str:
    return v.replace("_", " ").title().replace("Pm ", "PM ")


@router.get(
    "/enums", response_model=Envelope[dict[str, list[EnumValue]]], summary="Every enum with display labels (for dropdowns / Flutter)"
)
async def get_enums(ctx: Ctx):
    data = {e.__name__: [EnumValue(value=m.value, label=_label(m.value)) for m in e] for e in _EXPOSED if issubclass(e, StrEnum)}
    return {"success": True, "data": data, "meta": {}}


@router.get("/company", response_model=Envelope[CompanyOut])
async def company(ctx: Ctx):
    return respond(
        ctx,
        CompanyOut(
            name=settings.COMPANY_NAME,
            financial_year=financial_year(),
            today=today().isoformat(),
            timezone=settings.TIMEZONE,
            grn_tolerance_pct=settings.GRN_TOLERANCE_PCT,
            maker_checker_on_posting=settings.MAKER_CHECKER_ON_POSTING,
        ),
    )
