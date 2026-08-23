"""The scheduled sweep — what makes ingestion self-sustaining.

Every crawl so far has been a manual trigger. This is the component that asks "what is due"
and enqueues it, so the catalog stays current without anybody watching.

Two properties worth stating, because both are easy to get wrong:

**The sweep records the attempt, not the outcome.** It marks `last_attempt_at` when it
enqueues, and the worker marks `last_success_at` when the fetch actually succeeds. Conflating
them would make a source that enqueues but always fails look permanently healthy — which is
precisely the silent-failure mode staleness alerting exists to catch.

**It is idempotent by interval.** Running the sweep twice in quick succession enqueues nothing
the second time, so a scheduler that double-fires or a manual run alongside a cron does not
double-crawl.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from bugmine.models import PrivacyScope, Source


@dataclass(frozen=True)
class SweepResult:
    considered: int
    due: int
    enqueued: int
    stale: list[str]
    """Sources with no successful run in several intervals. A dead source produces no errors,
    so this list is the only signal that one has stopped working."""


def find_due(session: Session, *, now: datetime | None = None) -> list[Source]:
    now = now or datetime.now(UTC)
    sources = session.execute(select(Source).where(Source.enabled.is_(True))).scalars().all()
    return [s for s in sources if s.is_due(now)]


def find_stale(session: Session, *, now: datetime | None = None, factor: int = 3) -> list[Source]:
    now = now or datetime.now(UTC)
    sources = session.execute(select(Source).where(Source.enabled.is_(True))).scalars().all()
    return [s for s in sources if s.is_stale(now, factor=factor)]


def sweep(session: Session, enqueue_fn, *, now: datetime | None = None) -> SweepResult:  # type: ignore[no-untyped-def]
    """Enqueue every due source.

    `enqueue_fn` is injected rather than imported so this is testable without Cloud Tasks —
    the scheduling logic is the part worth testing, and it should not require a queue to run.
    """
    now = now or datetime.now(UTC)
    all_enabled = session.execute(select(Source).where(Source.enabled.is_(True))).scalars().all()
    due = [s for s in all_enabled if s.is_due(now)]

    enqueued = 0
    for source in due:
        payload = {
            "url": source.url,
            "subject_domain": source.subject_domain.value,
            "component_ref": source.component_ref,
            "ecosystem": source.ecosystem,
        }
        try:
            enqueue_fn(payload)
        except Exception:
            # A queue failure must not mark the source attempted, or it will be skipped for a
            # whole interval on the strength of work that never started.
            continue
        source.last_attempt_at = now
        enqueued += 1

    stale = [s.url for s in all_enabled if s.is_stale(now)]
    return SweepResult(considered=len(all_enabled), due=len(due), enqueued=enqueued, stale=stale)


def record_success(
    session: Session, url: str, *, tenant_id=None, now: datetime | None = None
) -> None:  # type: ignore[no-untyped-def]
    """Called by the crawl worker after a successful fetch."""
    now = now or datetime.now(UTC)
    source = (
        session.execute(select(Source).where(Source.url == url, Source.tenant_id == tenant_id))
        .scalars()
        .first()
    )
    if source is None:
        return
    source.last_success_at = now
    source.consecutive_failures = 0


def record_failure(session: Session, url: str, *, tenant_id=None) -> None:  # type: ignore[no-untyped-def]
    source = (
        session.execute(select(Source).where(Source.url == url, Source.tenant_id == tenant_id))
        .scalars()
        .first()
    )
    if source is not None:
        source.consecutive_failures += 1


def add_source(
    session: Session,
    *,
    url: str,
    subject_domain,  # type: ignore[no-untyped-def]
    component_ref: str | None = None,
    ecosystem: str | None = None,
    interval_minutes: int = 1440,
    privacy_scope: PrivacyScope = PrivacyScope.PUBLIC,
    tenant_id=None,  # type: ignore[no-untyped-def]
) -> Source:
    if (privacy_scope is PrivacyScope.TENANT) != (tenant_id is not None):
        raise ValueError("tenant scope requires a tenant, and a tenant requires tenant scope")
    source = Source(
        url=url,
        subject_domain=subject_domain,
        component_ref=component_ref,
        ecosystem=ecosystem,
        interval_minutes=interval_minutes,
        privacy_scope=privacy_scope,
        tenant_id=tenant_id,
    )
    session.add(source)
    session.flush()
    return source
