"""Transactional documents: market rates, BOQ, GRN, RFQ, issue notes, consumption, returns, adjustments."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AMOUNT, PCT, QTY, RATE, Authored, Base, Timestamps, UUIDPk, Versioned, enum_check
from app.models.enums import (
    AdjustmentReason,
    AdjustmentStatus,
    ApprovalAction,
    BoqStatus,
    EntryStatus,
    GrnStatus,
    IssueStatus,
    Priority,
    RateSource,
    ReturnStatus,
    RfqStatus,
)
from app.models.masters import Material, Project, Site, Store, Vendor


def _fk(table: str, nullable: bool = False, ondelete: str | None = None):
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable)


# ---------------------------------------------------------------- Market research


class MarketRate(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "market_rates"

    material_id: Mapped[uuid.UUID] = _fk("materials")
    vendor_id: Mapped[uuid.UUID | None] = _fk("vendors", nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    rate: Mapped[Decimal] = mapped_column(RATE)  # per material base UOM
    quoted_on: Mapped[date] = mapped_column(Date)
    valid_till: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(String(120))
    remarks: Mapped[str | None] = mapped_column(Text)

    material: Mapped[Material] = relationship(lazy="joined")
    vendor: Mapped[Vendor | None] = relationship(lazy="joined")

    __table_args__ = (
        enum_check("source", RateSource),
        CheckConstraint("rate > 0", name="rate_positive"),
        Index("ix_market_rates_material_date", "material_id", "quoted_on"),
    )


# ---------------------------------------------------------------- BOQ (central store master)


class Boq(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "boqs"

    boq_no: Mapped[str] = mapped_column(String(30), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    financial_year: Mapped[str] = mapped_column(String(7))  # "2026-27"
    revision: Mapped[int] = mapped_column(Integer, default=0)
    root_boq_id: Mapped[uuid.UUID | None] = _fk("boqs", nullable=True)
    parent_boq_id: Mapped[uuid.UUID | None] = _fk("boqs", nullable=True)
    status: Mapped[str] = mapped_column(String(12), default=BoqStatus.DRAFT)
    total_estimated_value: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    remarks: Mapped[str | None] = mapped_column(Text)
    submitted_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["BoqItem"]] = relationship(
        # Not auto-loaded: GRNs/reports join to Boq and must not drag every line along. Load explicitly.
        back_populates="boq",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
        order_by="BoqItem.line_no",
    )

    __table_args__ = (
        enum_check("status", BoqStatus),
        # Exactly one live (approved) central BOQ per financial year. Revisions supersede the parent.
        Index("uq_boqs_one_approved_per_fy", "financial_year", unique=True, postgresql_where=text("status = 'APPROVED'")),
        Index("ix_boqs_root", "root_boq_id"),
    )


class BoqItem(UUIDPk, Base):
    __tablename__ = "boq_items"

    boq_id: Mapped[uuid.UUID] = _fk("boqs", ondelete="CASCADE")
    line_no: Mapped[int] = mapped_column(Integer)
    material_id: Mapped[uuid.UUID] = _fk("materials")
    planned_qty: Mapped[Decimal] = mapped_column(QTY)
    estimated_rate: Mapped[Decimal] = mapped_column(RATE)
    estimated_amount: Mapped[Decimal] = mapped_column(AMOUNT)
    benchmark_rate: Mapped[Decimal | None] = mapped_column(RATE)
    remarks: Mapped[str | None] = mapped_column(Text)

    boq: Mapped[Boq] = relationship(back_populates="items")
    material: Mapped[Material] = relationship(lazy="joined")
    allocations: Mapped[list["BoqAllocation"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("boq_id", "material_id"),
        CheckConstraint("planned_qty > 0", name="planned_positive"),
        CheckConstraint("estimated_rate >= 0", name="rate_non_negative"),
    )


class BoqAllocation(UUIDPk, Base):
    __tablename__ = "boq_allocations"

    boq_item_id: Mapped[uuid.UUID] = _fk("boq_items", ondelete="CASCADE")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    site_id: Mapped[uuid.UUID | None] = _fk("sites", nullable=True)
    allocated_qty: Mapped[Decimal] = mapped_column(QTY)

    project: Mapped[Project] = relationship(lazy="joined")
    site: Mapped[Site | None] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("boq_item_id", "project_id", "site_id", postgresql_nulls_not_distinct=True),
        CheckConstraint("allocated_qty > 0", name="allocated_positive"),
    )


# ---------------------------------------------------------------- GRN (inward)


class Grn(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "grns"

    grn_no: Mapped[str] = mapped_column(String(30), unique=True)
    store_id: Mapped[uuid.UUID] = _fk("stores")
    vendor_id: Mapped[uuid.UUID] = _fk("vendors")
    boq_id: Mapped[uuid.UUID] = _fk("boqs")
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # reserved for Purchase Orders (v2)
    received_date: Mapped[date] = mapped_column(Date)
    invoice_no: Mapped[str | None] = mapped_column(String(40))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    challan_no: Mapped[str | None] = mapped_column(String(40))
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    remarks: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=GrnStatus.DRAFT)
    total_amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    total_gst: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    posted_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    vendor: Mapped[Vendor] = relationship(lazy="joined")
    store: Mapped[Store] = relationship(lazy="joined")
    boq: Mapped[Boq] = relationship(lazy="joined", innerjoin=True)
    items: Mapped[list["GrnItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="GrnItem.line_no")

    __table_args__ = (
        enum_check("status", GrnStatus),
        Index("ix_grns_status_date", "status", "received_date"),
        Index("ix_grns_boq", "boq_id"),
    )


class GrnItem(UUIDPk, Base):
    __tablename__ = "grn_items"

    grn_id: Mapped[uuid.UUID] = _fk("grns", ondelete="CASCADE")
    line_no: Mapped[int] = mapped_column(Integer)
    material_id: Mapped[uuid.UUID] = _fk("materials")
    received_qty: Mapped[Decimal] = mapped_column(QTY)
    rejected_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    accepted_qty: Mapped[Decimal] = mapped_column(QTY)
    rate: Mapped[Decimal] = mapped_column(RATE)  # excl. GST; stock is valued at this rate
    gst_rate: Mapped[Decimal] = mapped_column(PCT, default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    gst_amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    rejection_reason: Mapped[str | None] = mapped_column(String(200))

    material: Mapped[Material] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("grn_id", "material_id"),
        CheckConstraint("received_qty > 0", name="received_positive"),
        CheckConstraint("rejected_qty >= 0 AND rejected_qty <= received_qty", name="rejected_range"),
        CheckConstraint("accepted_qty = received_qty - rejected_qty", name="accepted_formula"),
        CheckConstraint("rate >= 0", name="rate_non_negative"),
    )


# ---------------------------------------------------------------- RFQ / Material request (site → store)


class MaterialRequest(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "material_requests"

    request_no: Mapped[str] = mapped_column(String(30), unique=True)
    project_id: Mapped[uuid.UUID] = _fk("projects")
    site_id: Mapped[uuid.UUID] = _fk("sites")
    store_id: Mapped[uuid.UUID] = _fk("stores")  # central store the request is addressed to
    request_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    required_by: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[str] = mapped_column(String(8), default=Priority.NORMAL)
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(18), default=RfqStatus.DRAFT)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(lazy="joined")
    site: Mapped[Site] = relationship(lazy="joined")
    store: Mapped[Store] = relationship(lazy="joined")
    items: Mapped[list["MaterialRequestItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="MaterialRequestItem.line_no"
    )

    __table_args__ = (
        enum_check("status", RfqStatus),
        enum_check("priority", Priority),
        Index("ix_material_requests_status", "status", "created_at"),
        Index("ix_material_requests_site", "site_id"),
    )


class MaterialRequestItem(UUIDPk, Base):
    __tablename__ = "material_request_items"

    request_id: Mapped[uuid.UUID] = _fk("material_requests", ondelete="CASCADE")
    line_no: Mapped[int] = mapped_column(Integer)
    material_id: Mapped[uuid.UUID] = _fk("materials")
    requested_qty: Mapped[Decimal] = mapped_column(QTY)
    approved_qty: Mapped[Decimal | None] = mapped_column(QTY)
    issued_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    remarks: Mapped[str | None] = mapped_column(Text)

    material: Mapped[Material] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("request_id", "material_id"),
        CheckConstraint("requested_qty > 0", name="requested_positive"),
        CheckConstraint("approved_qty IS NULL OR (approved_qty >= 0 AND approved_qty <= requested_qty)", name="approved_range"),
        CheckConstraint("issued_qty >= 0", name="issued_non_negative"),
    )


class Approval(UUIDPk, Base):
    """Approval / workflow history for any document."""

    __tablename__ = "approvals"

    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    stage: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(12))
    actor_id: Mapped[uuid.UUID] = _fk("users")
    comment: Mapped[str | None] = mapped_column(Text)
    acted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (enum_check("action", ApprovalAction), Index("ix_approvals_entity", "entity_type", "entity_id"))


# ---------------------------------------------------------------- Issue note (store → site)


class IssueNote(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "issue_notes"

    issue_no: Mapped[str] = mapped_column(String(30), unique=True)
    request_id: Mapped[uuid.UUID] = _fk("material_requests")
    from_store_id: Mapped[uuid.UUID] = _fk("stores")
    to_store_id: Mapped[uuid.UUID] = _fk("stores")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    site_id: Mapped[uuid.UUID] = _fk("sites")
    issue_date: Mapped[date] = mapped_column(Date)
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    driver_name: Mapped[str | None] = mapped_column(String(80))
    driver_phone: Mapped[str | None] = mapped_column(String(20))
    remarks: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=IssueStatus.DRAFT)
    total_value: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    issued_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_remarks: Mapped[str | None] = mapped_column(Text)
    cancelled_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    request: Mapped[MaterialRequest] = relationship(lazy="joined")
    project: Mapped[Project] = relationship(lazy="joined")
    site: Mapped[Site] = relationship(lazy="joined")
    items: Mapped[list["IssueNoteItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="IssueNoteItem.line_no")

    __table_args__ = (
        enum_check("status", IssueStatus),
        Index("ix_issue_notes_status_date", "status", "issue_date"),
        Index("ix_issue_notes_site", "site_id"),
        Index("ix_issue_notes_request", "request_id"),
    )


class IssueNoteItem(UUIDPk, Base):
    __tablename__ = "issue_note_items"

    issue_note_id: Mapped[uuid.UUID] = _fk("issue_notes", ondelete="CASCADE")
    line_no: Mapped[int] = mapped_column(Integer)
    request_item_id: Mapped[uuid.UUID] = _fk("material_request_items")
    material_id: Mapped[uuid.UUID] = _fk("materials")
    issued_qty: Mapped[Decimal] = mapped_column(QTY)
    rate: Mapped[Decimal | None] = mapped_column(RATE)  # weighted avg at the moment of issue
    amount: Mapped[Decimal | None] = mapped_column(AMOUNT)
    received_qty: Mapped[Decimal | None] = mapped_column(QTY)
    shortage_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    damaged_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))

    material: Mapped[Material] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("issued_qty > 0", name="issued_positive"),
        CheckConstraint("shortage_qty >= 0 AND damaged_qty >= 0", name="loss_non_negative"),
    )


# ---------------------------------------------------------------- Site operations


class ConsumptionEntry(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "consumption_entries"

    site_id: Mapped[uuid.UUID] = _fk("sites")
    store_id: Mapped[uuid.UUID] = _fk("stores")
    entry_date: Mapped[date] = mapped_column(Date)
    material_id: Mapped[uuid.UUID] = _fk("materials")
    qty: Mapped[Decimal] = mapped_column(QTY)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    amount: Mapped[Decimal | None] = mapped_column(AMOUNT)
    activity: Mapped[str | None] = mapped_column(String(200))
    remarks: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default=EntryStatus.POSTED)
    cancelled_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    material: Mapped[Material] = relationship(lazy="joined")
    site: Mapped[Site] = relationship(lazy="joined")

    __table_args__ = (
        enum_check("status", EntryStatus),
        CheckConstraint("qty > 0", name="qty_positive"),
        Index("ix_consumption_site_date", "site_id", "entry_date"),
    )


class ReturnNote(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "return_notes"

    return_no: Mapped[str] = mapped_column(String(30), unique=True)
    site_id: Mapped[uuid.UUID] = _fk("sites")
    project_id: Mapped[uuid.UUID] = _fk("projects")
    from_store_id: Mapped[uuid.UUID] = _fk("stores")
    to_store_id: Mapped[uuid.UUID] = _fk("stores")
    return_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(200))
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    remarks: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=ReturnStatus.DRAFT)
    dispatched_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    site: Mapped[Site] = relationship(lazy="joined")
    items: Mapped[list["ReturnNoteItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="ReturnNoteItem.line_no")

    __table_args__ = (enum_check("status", ReturnStatus), Index("ix_return_notes_site", "site_id"))


class ReturnNoteItem(UUIDPk, Base):
    __tablename__ = "return_note_items"

    return_note_id: Mapped[uuid.UUID] = _fk("return_notes", ondelete="CASCADE")
    line_no: Mapped[int] = mapped_column(Integer)
    material_id: Mapped[uuid.UUID] = _fk("materials")
    qty: Mapped[Decimal] = mapped_column(QTY)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    received_qty: Mapped[Decimal | None] = mapped_column(QTY)

    material: Mapped[Material] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("return_note_id", "material_id"), CheckConstraint("qty > 0", name="qty_positive"))


class StockAdjustment(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "stock_adjustments"

    adj_no: Mapped[str] = mapped_column(String(30), unique=True)
    store_id: Mapped[uuid.UUID] = _fk("stores")
    adj_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(16))
    remarks: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=AdjustmentStatus.DRAFT)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[uuid.UUID | None] = _fk("users", nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    store: Mapped[Store] = relationship(lazy="joined")
    items: Mapped[list["StockAdjustmentItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="StockAdjustmentItem.line_no"
    )

    __table_args__ = (enum_check("status", AdjustmentStatus), enum_check("reason", AdjustmentReason))


class StockAdjustmentItem(UUIDPk, Base):
    __tablename__ = "stock_adjustment_items"

    adjustment_id: Mapped[uuid.UUID] = _fk("stock_adjustments", ondelete="CASCADE")
    line_no: Mapped[int] = mapped_column(Integer)
    material_id: Mapped[uuid.UUID] = _fk("materials")
    physical_qty: Mapped[Decimal | None] = mapped_column(QTY)  # PHYSICAL_COUNT
    system_qty: Mapped[Decimal | None] = mapped_column(QTY)  # captured at approval
    qty_delta: Mapped[Decimal | None] = mapped_column(QTY)  # signed; computed for PHYSICAL_COUNT
    rate: Mapped[Decimal | None] = mapped_column(RATE)  # required for OPENING
    remarks: Mapped[str | None] = mapped_column(String(200))

    material: Mapped[Material] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("adjustment_id", "material_id"),)
