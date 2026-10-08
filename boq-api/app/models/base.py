import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, MetaData, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Quantities, rates and money are always NUMERIC — never float.
QTY = Numeric(18, 3)
RATE = Numeric(18, 4)
AMOUNT = Numeric(18, 2)
VALUE = Numeric(20, 4)
PCT = Numeric(5, 2)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    # Fetch server-generated values (updated_at, defaults) via RETURNING on flush, so nothing is left
    # expired — an expired attribute would trigger implicit IO, which async SQLAlchemy forbids.
    __mapper_args__ = {"eager_defaults": True}


def enum_check(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    values = ", ".join(f"'{e.value}'" for e in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or f"{column}_valid")


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Authored:
    @declared_attr
    def created_by_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    @declared_attr
    def updated_by_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class Versioned:
    """Optimistic-lock counter. Clients send it back on update; a mismatch returns 409 VERSION_CONFLICT."""

    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
