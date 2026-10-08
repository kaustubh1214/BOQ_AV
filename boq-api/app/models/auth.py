import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk, enum_check
from app.models.enums import Platform, ScopeType


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Bumped on password change / "log out everywhere"; access tokens carrying an older value are rejected.
    token_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assignments: Mapped[list["UserRoleAssignment"]] = relationship(back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class Permission(Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    module: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(200))


class Role(UUIDPk, Timestamps, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Which scope types make sense for this role (UI hint + validation), e.g. "SITE" for Site Engineer.
    default_scope: Mapped[str] = mapped_column(String(10), default=ScopeType.GLOBAL)

    permissions: Mapped[list["RolePermission"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (enum_check("default_scope", ScopeType),)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(String(64), ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True)


class UserRoleAssignment(UUIDPk, Base):
    __tablename__ = "user_role_assignments"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"))
    scope_type: Mapped[str] = mapped_column(String(10))
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="assignments")
    role: Mapped[Role] = relationship(lazy="selectin")

    __table_args__ = (
        enum_check("scope_type", ScopeType),
        UniqueConstraint("user_id", "role_id", "scope_type", "scope_id", postgresql_nulls_not_distinct=True),
        Index("ix_user_role_assignments_user", "user_id"),
    )


class UserSession(UUIDPk, Base):
    """One row per refresh token. Rotation creates a new row in the same family; reuse of a rotated token
    revokes the whole family (stolen-token detection)."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device_name: Mapped[str | None] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(10), default=Platform.WEB)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        enum_check("platform", Platform),
        Index("ix_user_sessions_user", "user_id"),
        Index("ix_user_sessions_family", "family_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(String(300))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_created", "created_at"),
        Index("ix_audit_logs_user", "user_id"),
    )
