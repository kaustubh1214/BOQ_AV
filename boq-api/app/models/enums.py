from enum import StrEnum


class ScopeType(StrEnum):
    GLOBAL = "GLOBAL"
    PROJECT = "PROJECT"
    SITE = "SITE"
    STORE = "STORE"


class Platform(StrEnum):
    WEB = "web"
    ANDROID = "android"
    IOS = "ios"


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"


class StoreType(StrEnum):
    CENTRAL = "CENTRAL"
    SITE = "SITE"


class RateSource(StrEnum):
    VENDOR_QUOTE = "VENDOR_QUOTE"
    MARKET_SURVEY = "MARKET_SURVEY"
    ONLINE = "ONLINE"


class BoqStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    CLOSED = "CLOSED"


class GrnStatus(StrEnum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    CANCELLED = "CANCELLED"


class RfqStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    RETURNED = "RETURNED"
    PM_APPROVED = "PM_APPROVED"
    STORE_APPROVED = "STORE_APPROVED"
    PARTIALLY_ISSUED = "PARTIALLY_ISSUED"
    ISSUED = "ISSUED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class Priority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    URGENT = "URGENT"


class IssueStatus(StrEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"  # in transit
    RECEIVED = "RECEIVED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"  # received with shortage / damage
    CANCELLED = "CANCELLED"


class ReturnStatus(StrEnum):
    DRAFT = "DRAFT"
    DISPATCHED = "DISPATCHED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class EntryStatus(StrEnum):
    POSTED = "POSTED"
    CANCELLED = "CANCELLED"


class AdjustmentReason(StrEnum):
    OPENING = "OPENING"
    PHYSICAL_COUNT = "PHYSICAL_COUNT"
    DAMAGE = "DAMAGE"
    WASTAGE = "WASTAGE"
    THEFT = "THEFT"


class AdjustmentStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ApprovalAction(StrEnum):
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class TxnType(StrEnum):
    INWARD = "INWARD"  # GRN posted (central +)
    ISSUE = "ISSUE"  # issue note posted (central −)
    RECEIPT = "RECEIPT"  # issue note received at site (site +)
    CONSUMPTION = "CONSUMPTION"  # site −
    RETURN_OUT = "RETURN_OUT"  # return dispatched (site −)
    RETURN_IN = "RETURN_IN"  # return received (central +)
    ADJUSTMENT = "ADJUSTMENT"  # ±, incl. opening stock
    REVERSAL = "REVERSAL"  # cancellation of any of the above


class DocType(StrEnum):
    BOQ = "BOQ"
    GRN = "GRN"
    RFQ = "RFQ"
    ISSUE = "ISN"
    RETURN = "RTN"
    ADJUSTMENT = "ADJ"
