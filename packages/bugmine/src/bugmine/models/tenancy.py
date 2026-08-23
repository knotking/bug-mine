"""Tenants, principals, and onboarding.

Onboarding is invite-only (FR-76 – FR-79): tenants are provisioned by an operator, users join
by invitation, and there is no domain-based auto-join. Most of what that buys is the absence of
work — no registration endpoint means no registration abuse surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow
from bugmine.models.enums import PrincipalKind, Role
from bugmine.models.types import pg_enum


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = pk_uuid()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    teams: Mapped[list[Team]] = relationship(back_populates="tenant")


class User(Base):
    """A person. Identity comes from the OIDC provider; this row is the local projection."""

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = pk_uuid()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    external_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)


class Team(Base):
    __tablename__ = "team"

    id: Mapped[uuid.UUID] = pk_uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    tenant: Mapped[Tenant] = relationship(back_populates="teams")

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="team_name_per_tenant"),)


class Membership(Base):
    __tablename__ = "membership"

    id: Mapped[uuid.UUID] = pk_uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team.id", ondelete="SET NULL"))
    role: Mapped[Role] = mapped_column(pg_enum(Role, "role"), nullable=False, default=Role.MEMBER)
    joined_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="one_membership_per_tenant"),
        Index("ix_membership_user", "user_id"),
    )


class Invite(Base):
    """FR-77, FR-78. Single-use, expiring, bound to an email address."""

    __tablename__ = "invite"

    id: Mapped[uuid.UUID] = pk_uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[Role] = mapped_column(pg_enum(Role, "role"), nullable=False, default=Role.MEMBER)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team.id", ondelete="SET NULL"))

    token_hash: Mapped[bytes] = mapped_column(nullable=False)
    """The token itself is never stored. A leaked database must not yield usable invites."""

    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    expires_at: Mapped[datetime] = tz_timestamp(nullable=False)
    accepted_at: Mapped[datetime | None] = tz_timestamp()
    revoked_at: Mapped[datetime | None] = tz_timestamp()
    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_invite_tenant_email", "tenant_id", "email"),
        Index("ix_invite_token", "token_hash"),
    )


class ApiKey(Base):
    """FR-80. Bound to exactly one principal — a user or a team, never a tenant."""

    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = pk_uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    principal_kind: Mapped[PrincipalKind] = mapped_column(
        pg_enum(PrincipalKind, "principal_kind"), nullable=False
    )
    principal_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    principal_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team.id"))

    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    """First characters of the key, for identification in a list. Not a secret."""
    secret_hash: Mapped[bytes] = mapped_column(nullable=False)

    last_used_at: Mapped[datetime | None] = tz_timestamp()
    expires_at: Mapped[datetime | None] = tz_timestamp()
    revoked_at: Mapped[datetime | None] = tz_timestamp()
    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    __table_args__ = (Index("ix_api_key_tenant", "tenant_id"),)
