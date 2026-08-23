"""Catalog tables: components, bug records, and their versions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow
from bugmine.models.enums import BugType, LifecycleState, RecordOrigin, SubjectDomain
from bugmine.models.types import pg_enum


class Component(Base):
    """The canonical name for one piece of software.

    This table is the retrieval join key. `postgres`, `postgresql`, `PostgreSQL` and `psql`
    all name one thing, and a mismatch between the name on a record and the name in a scanned
    manifest produces **no error** — just an empty result that reads as good news. Hence a
    registry with aliases resolved at both write and read time, rather than free-text names on
    either side.
    """

    __tablename__ = "component"

    id: Mapped[uuid.UUID] = pk_uuid()
    subject_domain: Mapped[SubjectDomain] = mapped_column(
        pg_enum(SubjectDomain, "subject_domain"), nullable=False
    )
    canonical_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    ecosystem: Mapped[str | None] = mapped_column(String(50))
    """e.g. pypi, npm — None for domains without a package registry."""

    aliases: Mapped[list[ComponentAlias]] = relationship(
        back_populates="component", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("subject_domain", "canonical_ref", name="component_identity"),
    )


class ComponentAlias(Base):
    __tablename__ = "component_alias"

    id: Mapped[uuid.UUID] = pk_uuid()
    component_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("component.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    """Stored lowercased. Resolution is case-insensitive."""

    component: Mapped[Component] = relationship(back_populates="aliases")

    __table_args__ = (UniqueConstraint("alias", name="alias_is_unambiguous"),)


class BugRecord(Base):
    """The stable identity of one known defect.

    Classified on **both** axes (FR-38): either alone is unreachable by one of the two
    retrieval paths.
    """

    __tablename__ = "bug_record"

    id: Mapped[uuid.UUID] = pk_uuid()
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("component.id"), nullable=False)
    subject_domain: Mapped[SubjectDomain] = mapped_column(
        pg_enum(SubjectDomain, "subject_domain"), nullable=False
    )
    bug_type: Mapped[BugType] = mapped_column(pg_enum(BugType, "bug_type"), nullable=False)

    applicability: Mapped[dict] = mapped_column(JSONB, nullable=False)
    """A serialised `models.applicability.Applicability`. Tagged by its `kind` field."""

    origin: Mapped[RecordOrigin] = mapped_column(
        pg_enum(RecordOrigin, "record_origin"), nullable=False
    )
    lifecycle_state: Mapped[LifecycleState] = mapped_column(
        pg_enum(LifecycleState, "lifecycle_state"),
        nullable=False,
        default=LifecycleState.ACTIVE,
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bug_record.id"))
    """FR-71 — citations to a superseded record resolve here rather than breaking."""

    identity_key: Mapped[str | None] = mapped_column(String(300), index=True)
    """FR-70 — groups records describing one defect across origins.

    Corroboration by "a matching public source" (FR-44) is an identity match, so without this
    cross-origin corroboration silently never fires. Resolution is unsolved (L1); this column
    exists so a conservative heuristic has somewhere to write.
    """

    first_seen_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    versions: Mapped[list[BugVersion]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_bug_record_retrieval", "subject_domain", "component_id"),
        Index("ix_bug_record_state", "lifecycle_state"),
    )


class BugVersion(Base):
    """One observed state of a record. FR-5."""

    __tablename__ = "bug_version"

    id: Mapped[uuid.UUID] = pk_uuid()
    record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bug_record.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(nullable=False)

    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """The dedup key, and the reason catalog size is bounded.

    A new version is written only when this changes. Without it, versioning mints a row per
    crawl of every source forever and catalog size becomes a function of crawl frequency
    rather than of how often the software actually changed.
    """

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    raw_artifact_uri: Mapped[str | None] = mapped_column(Text)
    """Provenance back to what was crawled — enables re-extraction without re-crawling, and
    lets a poisoned batch be identified and retracted as a set (ADR-0005 control 5)."""

    observed_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    record: Mapped[BugRecord] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("record_id", "version_no", name="version_sequence"),
        UniqueConstraint("record_id", "content_hash", name="dedup_by_content"),
        Index("ix_bug_version_observed", "observed_at"),
    )
