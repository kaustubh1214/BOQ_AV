import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import QTY, RATE, VALUE, Base, UUIDPk, enum_check
from app.models.enums import TxnType


class StockLedger(Base):
    """Append-only movement log (a DB trigger rejects UPDATE/DELETE). Source of truth for every stock figure."""

    __tablename__ = "stock_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id"))
    material_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("materials.id"))
    txn_date: Mapped[date] = mapped_column(Date)
    txn_type: Mapped[str] = mapped_column(String(12))
    ref_type: Mapped[str] = mapped_column(String(20))
    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ref_no: Mapped[str | None] = mapped_column(String(30))
    qty_in: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    qty_out: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    rate: Mapped[Decimal] = mapped_column(RATE)
    value: Mapped[Decimal] = mapped_column(VALUE)  # signed (+ in, − out)
    balance_qty: Mapped[Decimal] = mapped_column(QTY)
    balance_value: Mapped[Decimal] = mapped_column(VALUE)
    avg_rate_after: Mapped[Decimal] = mapped_column(RATE)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        enum_check("txn_type", TxnType),
        CheckConstraint("qty_in >= 0 AND qty_out >= 0 AND (qty_in > 0) <> (qty_out > 0)", name="one_direction"),
        Index("ix_stock_ledger_store_material", "store_id", "material_id", "id"),
        Index("ix_stock_ledger_ref", "ref_type", "ref_id"),
        Index("ix_stock_ledger_date", "txn_date"),
    )


class StockBalance(Base):
    __tablename__ = "stock_balances"

    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id"), primary_key=True)
    material_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("materials.id"), primary_key=True)
    qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    value: Mapped[Decimal] = mapped_column(VALUE, default=Decimal("0"))
    avg_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0"))
    last_movement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Last line of defence: stock can never go negative, even if application code is wrong.
        CheckConstraint("qty >= 0", name="qty_non_negative"),
        CheckConstraint("value >= 0", name="value_non_negative"),
    )


class DocumentSequence(Base):
    __tablename__ = "document_sequences"

    doc_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    financial_year: Mapped[str] = mapped_column(String(7), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class Attachment(UUIDPk, Base):
    __tablename__ = "attachments"

    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    storage_key: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(60))
    size: Mapped[int] = mapped_column(Integer)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_attachments_entity", "entity_type", "entity_id"),)


class Notification(UUIDPk, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(String(500))
    entity_type: Mapped[str | None] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "read_at", "created_at"),)


class IdempotencyKey(Base):
    """Replays the stored response when a client (e.g. Flutter on a flaky site network) retries a create."""

    __tablename__ = "idempotency_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceToken(UUIDPk, Base):
    """Push-notification tokens (FCM) for the Flutter app."""

    __tablename__ = "device_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(300), unique=True)
    platform: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
