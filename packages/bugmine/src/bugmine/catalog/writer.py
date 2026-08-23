"""The catalog write path — the sole writer of bug records.

Every origin converges here: crawling, scans, and later evals. That convergence is what makes
the plane split in the architecture work, and it is why dedup and scope enforcement live in one
place rather than in each producer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bugmine.catalog import components
from bugmine.catalog.hashing import content_hash
from bugmine.models import (
    BugRecord,
    BugType,
    BugVersion,
    LifecycleState,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
)


@dataclass(frozen=True)
class IncomingBug:
    """A record as an extractor produces it, before it has an identity in the catalog."""

    subject_domain: SubjectDomain
    component_ref: str
    bug_type: BugType
    applicability: dict[str, Any]
    title: str
    description: str | None = None
    evidence_url: str | None = None
    ecosystem: str | None = None
    aliases: tuple[str, ...] = ()
    identity_key: str | None = None
    raw_artifact_uri: str | None = None


@dataclass(frozen=True)
class WriteResult:
    record_id: uuid.UUID
    version_id: uuid.UUID | None
    created_record: bool
    created_version: bool

    @property
    def deduplicated(self) -> bool:
        """True when the content was unchanged and nothing was written."""
        return not self.created_version


def write(
    session: Session,
    incoming: IncomingBug,
    *,
    origin: RecordOrigin,
    privacy_scope: PrivacyScope = PrivacyScope.PUBLIC,
    tenant_id: uuid.UUID | None = None,
) -> WriteResult:
    """Upsert a bug record, creating a new version only when the content actually changed.

    Scope and tenant are arguments rather than fields on `IncomingBug` on purpose: an extractor
    reports what it found, and *who may see it* is decided by the job's origin. Letting the
    extractor choose would put a privacy decision in the path most exposed to injected content.
    """
    if (privacy_scope is PrivacyScope.TENANT) != (tenant_id is not None):
        raise ValueError("tenant scope requires a tenant, and a tenant requires tenant scope")

    component = components.get_or_create(
        session,
        subject_domain=incoming.subject_domain,
        ref=incoming.component_ref,
        ecosystem=incoming.ecosystem,
        aliases=list(incoming.aliases),
    )

    record = (
        session.execute(
            select(BugRecord).where(
                BugRecord.component_id == component.id,
                BugRecord.bug_type == incoming.bug_type,
                BugRecord.privacy_scope == privacy_scope,
                BugRecord.identity_key == incoming.identity_key
                if incoming.identity_key is not None
                else BugRecord.applicability == incoming.applicability,
            )
        )
        .scalars()
        .first()
    )

    created_record = record is None
    if record is None:
        record = BugRecord(
            component_id=component.id,
            subject_domain=incoming.subject_domain,
            bug_type=incoming.bug_type,
            applicability=incoming.applicability,
            origin=origin,
            lifecycle_state=LifecycleState.ACTIVE,
            privacy_scope=privacy_scope,
            tenant_id=tenant_id,
            identity_key=incoming.identity_key,
        )
        session.add(record)
        session.flush()

    digest = content_hash(
        title=incoming.title,
        description=incoming.description,
        evidence_url=incoming.evidence_url,
        applicability=incoming.applicability,
    )

    # The dedup gate. A re-crawl of unchanged content stops here.
    existing = (
        session.execute(
            select(BugVersion).where(
                BugVersion.record_id == record.id,
                BugVersion.content_hash == digest,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return WriteResult(record.id, existing.id, created_record, created_version=False)

    next_no = (
        session.execute(
            select(func.coalesce(func.max(BugVersion.version_no), 0)).where(
                BugVersion.record_id == record.id
            )
        ).scalar_one()
        + 1
    )

    version = BugVersion(
        record_id=record.id,
        version_no=next_no,
        content_hash=digest,
        title=incoming.title,
        description=incoming.description,
        evidence_url=incoming.evidence_url,
        raw_artifact_uri=incoming.raw_artifact_uri,
    )
    session.add(version)
    session.flush()

    return WriteResult(record.id, version.id, created_record, created_version=True)
