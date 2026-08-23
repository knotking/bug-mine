"""The catalog read path.

Two operations that look alike and are not:

- **Retrieval** — "does anything affect `couchbase@7.2.1`?" An indexed exact match on component,
  then applicability evaluated in application code. This is the hot path.
- **Search** — "what is known about Kafka?" Free text, exploratory, comparatively rare.

Conflating them means either running full-text for what is an exact lookup, or forcing
exploratory search through a matcher that needs an exact component name.

Note what is *not* here: a scope filter. Row-level security already restricts what a session
can see, so retrieval returns public plus subscriber plus own-tenant records by
construction. Adding an application-side filter would duplicate the policy and invite the two
to drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from bugmine.catalog import components
from bugmine.models import (
    Applicability,
    BugRecord,
    BugType,
    BugVersion,
    Component,
    LifecycleState,
    MatchResult,
    SubjectDomain,
)

_applicability = TypeAdapter(Applicability)


@dataclass(frozen=True)
class Query:
    subject_domain: SubjectDomain
    ref: str
    version: str | None = None
    ecosystem: str | None = None


@dataclass(frozen=True)
class Match:
    record: BugRecord
    version: BugVersion
    result: MatchResult

    @property
    def version_confirmed(self) -> bool:
        return self.result.confirmed


@dataclass(frozen=True)
class NotCovered:
    query: Query
    reason: str


def current_version(session: Session, record: BugRecord) -> BugVersion | None:
    return (
        session.execute(
            select(BugVersion)
            .where(BugVersion.record_id == record.id)
            .order_by(BugVersion.version_no.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def retrieve(
    session: Session,
    query: Query,
    *,
    bug_types: list[BugType] | None = None,
    at: datetime | None = None,
) -> tuple[list[Match], NotCovered | None]:
    """Find records affecting one component at one version.

    Returns `NotCovered` rather than an empty list when the component is absent from the
    catalog entirely (FR-39). The distinction is the whole point: silence would read as a clean
    bill of health, and early on it almost always means "we don't know" rather than "you're
    fine".
    """
    at = at or datetime.now(UTC)

    component: Component | None = components.resolve(
        session,
        subject_domain=query.subject_domain,
        ref=query.ref,
        ecosystem=query.ecosystem,
    )
    if component is None:
        return [], NotCovered(query, "component_not_in_catalog")

    stmt = select(BugRecord).where(
        BugRecord.component_id == component.id,
        BugRecord.lifecycle_state.in_(
            [LifecycleState.ACTIVE, LifecycleState.FIXED, LifecycleState.DISPUTED]
        ),
    )
    if bug_types:
        stmt = stmt.where(BugRecord.bug_type.in_(bug_types))

    matches: list[Match] = []
    for record in session.execute(stmt).scalars():
        applicability = _applicability.validate_python(record.applicability)
        # TimeWindow and ModelRevision are date-bounded; the others ignore `at`.
        try:
            result = applicability.matches(query.version, at=at)  # type: ignore[call-arg]
        except TypeError:
            result = applicability.matches(query.version)

        if not result.matched:
            continue
        version = current_version(session, record)
        if version is None:
            continue
        matches.append(Match(record=record, version=version, result=result))

    return matches, None
