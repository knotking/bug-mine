"""Catalog tables: components, bug records, and their versions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow
from bugmine.models.enums import (
    BugType,
    LifecycleState,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
)
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

    privacy_scope: Mapped[PrivacyScope] = mapped_column(
        pg_enum(PrivacyScope, "privacy_scope"), nullable=False, default=PrivacyScope.PUBLIC
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    """Set only when privacy_scope is TENANT.

    A tenant-scoped record with no tenant is a bug, not a default — enforced by a check
    constraint below rather than by convention, because the failure mode is a private record
    becoming visible to everyone.
    """
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
        Index("ix_bug_record_scope", "privacy_scope", "tenant_id"),
        CheckConstraint(
            "(privacy_scope = 'tenant') = (tenant_id IS NOT NULL)",
            name="tenant_scope_requires_tenant",
        ),
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


class Source(Base):
    """A configured crawl source.

    Scope is a property of the source, not of the worker: an admin source produces `public`
    records, a tenant source produces `tenant` records, and the same pipeline serves both. That
    is what keeps ingestion one code path rather than two.

    `last_success_at` is separate from `last_attempt_at` on purpose. A source that fails
    silently produces no errors and no records, and the catalog keeps serving stale data — so
    staleness is measured by absence of success, which is the only signal that failure mode
    generates.
    """

    __tablename__ = "source"

    id: Mapped[uuid.UUID] = pk_uuid()
    url: Mapped[str] = mapped_column(Text, nullable=False)
    subject_domain: Mapped[SubjectDomain] = mapped_column(
        pg_enum(SubjectDomain, "subject_domain"), nullable=False
    )
    component_ref: Mapped[str | None] = mapped_column(String(200))
    ecosystem: Mapped[str | None] = mapped_column(String(50))

    privacy_scope: Mapped[PrivacyScope] = mapped_column(
        pg_enum(PrivacyScope, "privacy_scope"), nullable=False, default=PrivacyScope.PUBLIC
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))

    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_attempt_at: Mapped[datetime | None] = tz_timestamp()
    last_success_at: Mapped[datetime | None] = tz_timestamp()
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("url", "tenant_id", name="source_is_unique_per_owner"),
        Index("ix_source_due", "enabled", "last_attempt_at"),
        CheckConstraint(
            "(privacy_scope = 'tenant') = (tenant_id IS NOT NULL)",
            name="source_tenant_scope_requires_tenant",
        ),
    )

    RETRY_BACKOFF_MINUTES = 10
    """First retry delay after a failed attempt, doubled per consecutive failure and capped at
    the source's own interval."""

    def is_due(self, now: datetime) -> bool:
        """Whether this source should be enqueued.

        A *failed* attempt must not satisfy the interval. The sweep marks a source attempted
        when it enqueues, not when the fetch succeeds — so 115 sources rate-limited in one burst
        were all stamped as attempted and went silent for a full day, having fetched nothing.
        The catalog stopped growing and every source looked healthy.

        So a source that has never succeeded, or whose last attempt failed, comes back on a
        backoff instead: soon enough to recover from a transient limit, with doubling so a
        genuinely broken source does not get retried forever at full rate.
        """
        if not self.enabled:
            return False
        if self.last_attempt_at is None:
            return True

        interval = self.interval_minutes * 60
        since_attempt = (now - self.last_attempt_at).total_seconds()
        if since_attempt >= interval:
            return True

        failing = self.consecutive_failures > 0 or self.last_success_at is None
        if not failing:
            return False
        # First failure waits the base delay, not double it: the common case is a transient
        # rate limit, and doubling before the first retry delays recovery for no reason.
        doublings = min(max(self.consecutive_failures - 1, 0), 6)
        backoff = min(self.RETRY_BACKOFF_MINUTES * 60 * (2**doublings), interval)
        return since_attempt >= backoff

    def is_stale(self, now: datetime, *, factor: int = 3) -> bool:
        """No successful run in `factor` intervals.

        The alerting condition from the operability requirements. A dead crawler produces no
        errors at all, so error rates cannot detect it — only absence of success can.
        """
        if not self.enabled:
            return False
        if self.last_success_at is None:
            return self.last_attempt_at is not None
        return (now - self.last_success_at).total_seconds() > self.interval_minutes * 60 * factor
