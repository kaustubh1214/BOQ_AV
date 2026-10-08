import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import PCT, QTY, Authored, Base, Timestamps, UUIDPk, Versioned, enum_check
from app.models.enums import ProjectStatus, StoreType


def _trgm(table: str, column: str = "name") -> Index:
    return Index(f"ix_{table}_{column}_trgm", column, postgresql_using="gin", postgresql_ops={column: "gin_trgm_ops"})


class MaterialCategory(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "material_categories"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("material_categories.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Uom(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "uoms"

    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class UomConversion(UUIDPk, Timestamps, Authored, Versioned, Base):
    """1 from_uom = factor × to_uom. material_id NULL = applies to all materials (e.g. 1 MT = 1000 KG)."""

    __tablename__ = "uom_conversions"

    material_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("materials.id"))
    from_uom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uoms.id"))
    to_uom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uoms.id"))
    factor: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("material_id", "from_uom_id", "to_uom_id", postgresql_nulls_not_distinct=True),
        CheckConstraint("factor > 0", name="factor_positive"),
    )


class Material(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "materials"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("material_categories.id"))
    base_uom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uoms.id"))
    hsn_code: Mapped[str | None] = mapped_column(String(12))
    gst_rate: Mapped[Decimal] = mapped_column(PCT, default=Decimal("18"), server_default="18")
    specification: Mapped[str | None] = mapped_column(Text)
    reorder_level: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"), server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    category: Mapped[MaterialCategory] = relationship(lazy="joined")
    base_uom: Mapped[Uom] = relationship(lazy="joined")

    __table_args__ = (_trgm("materials"), Index("ix_materials_category", "category_id"))


class Vendor(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "vendors"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (_trgm("vendors"),)


class Project(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "projects"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=ProjectStatus.ACTIVE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (enum_check("status", ProjectStatus),)


class Site(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "sites"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    status: Mapped[str] = mapped_column(String(12), default=ProjectStatus.ACTIVE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    project: Mapped[Project] = relationship(lazy="joined")

    __table_args__ = (enum_check("status", ProjectStatus), Index("ix_sites_project", "project_id"))


class Store(UUIDPk, Timestamps, Authored, Versioned, Base):
    __tablename__ = "stores"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(10))
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sites.id"), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (enum_check("type", StoreType),)
