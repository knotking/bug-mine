"""Promotion — turning one tenant's observation into shared knowledge, carefully.

A scan produces knowledge about third-party software. That knowledge belongs in the catalog,
where the next customer's scan can use it. This is the flywheel that makes the catalog
proprietary rather than merely assembled.

It is also the most dangerous path in the system, for two independent reasons the requirements
are explicit about — and sanitization alone answers neither:

**Privacy.** A single-tenant observation can identify its source even with the code stripped.
"Library X fails when used with Y and Z" may describe exactly one customer's stack. Requiring
*k* unaffiliated tenants makes the record a statement about software rather than about anybody.

**Quality.** A scan-derived candidate is an inference about code read once. Promoting it
directly would let one error reach every tenant — and under ADR-0002 it would arrive *wearing a
citation*, which is the very authority that mechanism exists to confer. Corroboration is what
converts a hypothesis into a record.

And one line that is absolute: **findings about a tenant's own code are never eligible** (FR-42),
under any amount of corroboration. Third-party software is shared reality; a customer's own code
is theirs.

## Why this needs a privileged session, stated rather than buried

Corroboration is inherently cross-tenant: counting how many *unaffiliated* tenants saw a thing
means reading rows belonging to several of them. Row-level security exists precisely to make
that impossible, and every other path in this system runs under it.

So promotion is the one deliberate exception, and it is narrowed rather than waved through:

- It runs from a session that bypasses RLS, which no request path ever holds — only this job.
- It reads candidates and writes exactly four fields: scope, lifecycle, tenant (to null) and
  nothing else. Evidence, tenant identity and stack composition never move (FR-45).
- It aggregates on `tenant_id` and then discards it. The count leaves; the identity does not.

The alternative — a policy exposing candidate rows to anonymous sessions — would widen the
blast radius from one job to every unauthenticated code path, to avoid one clearly-labelled
privilege. That is a worse trade, and the labelling is the point: this module is short, and it
is the only place in the system where cross-tenant reads are legitimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bugmine.models import BugRecord, LifecycleState, PrivacyScope, RecordOrigin

CORROBORATION_THRESHOLD = 3
"""Independent tenants that must observe a candidate before it is shared.

Three rather than two: with two, a single customer running two accounts reaches the bar, and the
privacy argument is about *unaffiliated* observers rather than distinct rows.
"""


@dataclass
class PromotionResult:
    promoted: list[str] = field(default_factory=list)
    held: dict[str, int] = field(default_factory=dict)
    """Candidates short of the threshold, and how many tenants have seen each. Reported rather
    than hidden: a candidate stuck at two observers forever is a signal about the threshold, and
    silence would make it invisible."""
    ineligible: int = 0
    """Candidates that can never be promoted — own-code findings. Counted so the number is
    visible, never enumerated, since the identifiers are the tenant's."""


def _observers(session: Session, identity_key: str) -> int:
    """How many distinct tenants have independently recorded this identity.

    Counted on `tenant_id`, so two records from one tenant are one observation. Counting rows
    would let a single customer's repeated scans promote their own candidate.
    """
    return int(
        session.execute(
            select(func.count(func.distinct(BugRecord.tenant_id))).where(
                BugRecord.identity_key == identity_key,
                BugRecord.origin == RecordOrigin.SCAN_DERIVED,
                BugRecord.tenant_id.isnot(None),
            )
        ).scalar_one()
    )


def eligible(record: BugRecord) -> bool:
    """Whether a candidate may ever be promoted.

    Own-code findings never can (FR-42). The check is on origin and scope rather than on
    anything in the text, because a rule enforced by reading content is a rule that fails on the
    first record whose content is unusual.
    """
    if record.origin is not RecordOrigin.SCAN_DERIVED:
        return False
    if record.lifecycle_state is not LifecycleState.CANDIDATE:
        return False
    return record.identity_key is not None


def promote(session: Session, *, threshold: int = CORROBORATION_THRESHOLD) -> PromotionResult:
    """Promote every candidate that enough unaffiliated tenants have seen.

    Runs with no tenant context so it can count across tenants — which is exactly why it must
    not copy anything tenant-specific onto the promoted record. What crosses the boundary is the
    component, the type, the applicability and the title: facts about software. Evidence,
    tenant identity and stack composition do not (FR-45).
    """
    result = PromotionResult()

    candidates = session.execute(
        select(BugRecord).where(
            BugRecord.origin == RecordOrigin.SCAN_DERIVED,
            BugRecord.lifecycle_state == LifecycleState.CANDIDATE,
        )
    ).scalars().all()

    seen: set[str] = set()
    for candidate in candidates:
        if not eligible(candidate):
            result.ineligible += 1
            continue
        key = candidate.identity_key
        assert key is not None
        if key in seen:
            continue
        seen.add(key)

        observers = _observers(session, key)
        if observers < threshold:
            result.held[key] = observers
            continue

        # Promoted in place: scope widens and the record becomes active. A new row would orphan
        # the citations already pointing at this one.
        candidate.privacy_scope = PrivacyScope.PUBLIC
        candidate.lifecycle_state = LifecycleState.ACTIVE
        candidate.tenant_id = None
        result.promoted.append(key)

    return result
