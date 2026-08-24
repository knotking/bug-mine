"""Scan findings and their citations.

The `FindingCitation` table is where ADR-0002 stops being a policy and becomes a schema
property: a finding with no citation row is, by definition, ungrounded. The provenance gate is
a check on whether this relationship is populated with IDs from the retrieved set — which is
why grounding survives a model change, a prompt change, or a long context, and prompt-level
instructions do not.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow
from bugmine.models.enums import BugType
from bugmine.models.types import pg_enum


class Scan(Base):
    __tablename__ = "scan"

    id: Mapped[uuid.UUID] = pk_uuid()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job.id"), nullable=False)
    repo_ref: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )

    grounding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """False when package resolution was incomplete (FR-14). A partial graph scanned silently
    looks identical to a clean one, so this must reach the user."""

    uncovered_components: Mapped[int] = mapped_column(default=0)
    unresolved_manifests: Mapped[int] = mapped_column(default=0, server_default="0")
    suppressed_unreachable: Mapped[int] = mapped_column(default=0, server_default="0")
    """Catalog matches removed because the project provably does not touch them. The number the
    product rests on: three findings out of three matches and three out of forty are different
    claims, and without this they look identical."""
    dependencies_scanned: Mapped[int] = mapped_column(default=0, server_default="0")
    """Manifests naming dependencies without pinning them — a `pyproject.toml` with no
    lockfile. Counted separately because zero findings over unread dependencies looks exactly
    like zero findings over a clean repository, and they are not the same answer."""
    """FR-39 — components with no catalog coverage. Reported explicitly; silence would read
    as a clean bill of health, which is the most likely way early users are misled."""

    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    findings: Mapped[list[Finding]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )


class Finding(Base):
    __tablename__ = "finding"

    id: Mapped[uuid.UUID] = pk_uuid()
    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scan.id", ondelete="CASCADE"), nullable=False
    )
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("component.id"), nullable=False)
    bug_type: Mapped[BugType] = mapped_column(pg_enum(BugType, "bug_type"), nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)

    reachable: Mapped[bool | None] = mapped_column(Boolean)
    """None means undetermined. Per ADR-0006 an undetermined finding is **reported with
    reduced confidence, never suppressed** — a false positive is dismissible, a false negative
    is a bug the user never hears about."""
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    version_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """False when applicability matched only because the version was unknown."""

    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    scan: Mapped[Scan] = relationship(back_populates="findings")
    citations: Mapped[list[FindingCitation]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_finding_scan", "scan_id"),)


class FindingCitation(Base):
    """The edge that implements FR-32 / ADR-0002.

    Multiple rows per finding are allowed on purpose: an interaction finding (FR-30) may rest
    on two records each covering half the claim.
    """

    __tablename__ = "finding_citation"

    id: Mapped[uuid.UUID] = pk_uuid()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finding.id", ondelete="CASCADE"), nullable=False
    )
    bug_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bug_version.id"), nullable=False)

    finding: Mapped[Finding] = relationship(back_populates="citations")

    __table_args__ = (
        UniqueConstraint("finding_id", "bug_version_id", name="citation_is_distinct"),
    )
