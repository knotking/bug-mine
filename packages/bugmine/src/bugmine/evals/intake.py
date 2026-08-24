"""Accepting eval results from runners outside this system, and deciding what they establish.

The runner in `runner.py` works in-process, which covers a suite we schedule ourselves and
nothing else. Evals are meant to be the origin that finds defects nobody wrote down, and most of
the people able to find those defects are not us — they are running a suite in their own CI
against their own traffic. This module is how that evidence gets in.

The whole design rests on one separation:

    an observation is evidence; a bug record is a claim

A single run of forty attempts reporting four failures does not establish a defect. It is
consistent with a 10% failure rate and equally consistent with a 3% one, and the difference
decides whether anybody should care. So submissions accumulate, and promotion is a separate
decision taken against the pooled evidence using the Wilson bound in `rate.py`.

Three rules are load-bearing, and each one exists because the obvious alternative silently
produces a false record:

**Pool only within an identical question.** The key includes probe version and target revision.
A probe whose prompt changed asks something else; pooling old results with new would let a
change in what we asked look like a change in how the target behaved.

**Submissions are idempotent.** A runner retrying after a timeout resubmits the same run. Left
to double-count, a retry storm manufactures corroboration out of one bad afternoon, and the
record it produces is indistinguishable from a real one.

**Refutation retracts.** Evidence accumulating past a promoted record can push the interval back
below the tolerated rate. Leaving the record standing would mean the catalog asserts a defect
its own evidence no longer supports — the exact failure the citation model exists to prevent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bugmine.catalog.writer import IncomingBug, write
from bugmine.evals.rate import FailureRate, runs_needed, wilson
from bugmine.models import (
    BugRecord,
    BugType,
    EvalObservation,
    LifecycleState,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
)

logger = logging.getLogger("bugmine.evals.intake")

MAX_SAMPLES = 3
"""Failing responses kept per submission. A probe run a thousand times would otherwise carry a
thousand copies of the same wrongness, and the evidence is not improved by the repetition."""

MAX_SAMPLE_CHARS = 2000


@dataclass(frozen=True)
class Submission:
    """One eval run, as a runner reports it."""

    run_id: str
    subject_domain: SubjectDomain
    component_ref: str
    probe: str
    probe_version: str
    runs: int
    failures: int
    errors: int = 0
    target_revision: str | None = None
    prompt_digest: str | None = None
    samples: tuple[str, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class Verdict:
    """What the pooled evidence supports, and what it would take to say more."""

    pooled_runs: int
    pooled_failures: int
    pooled_errors: int
    behavioural_runs: int
    rate: FailureRate
    accepted: bool
    """False when this exact run had already been submitted. Not an error — the caller retried,
    and the correct response to a retry is the same answer, not a second count."""
    record_id: uuid.UUID | None = None
    retracted: bool = False

    @property
    def state(self) -> str:
        if self.rate.corroborated:
            return "corroborated"
        return "refuted" if self.rate.refuted else "undetermined"

    @property
    def runs_to_decide(self) -> int:
        """Roughly how many total runs it would take to settle a defect at the observed rate.

        Returned so a runner can schedule rather than guess. A suite that stops at forty and
        reports "nothing found" has, for a 1-in-100 defect, gathered evidence that could never
        have shown one — and reported that as reassurance.
        """
        if self.rate.corroborated or self.behavioural_runs <= 0:
            return 0
        return runs_needed(self.rate.observed) if self.rate.observed > 0 else 0


def _pool_filters(sub_or_key, scope: PrivacyScope, tenant_id: uuid.UUID | None):  # type: ignore[no-untyped-def]
    """The pooling key: the same question asked of the same thing.

    Probe, probe version, component and target revision are load-bearing here — remove any one
    and results answering different questions get summed, which I verified by removing each in
    turn and watching a test fail.

    Scope and tenant are *not* load-bearing in this function: row-level security already makes a
    tenant's rows invisible to anyone else, so removing these filters changes nothing
    observable. They stay as defence in depth and as documentation of intent, but the guarantee
    lives in the database. `test_eval_observation_has_row_level_security` is what actually pins
    it, because it is the layer that would have to fail.
    """
    return (
        EvalObservation.subject_domain == sub_or_key.subject_domain,
        EvalObservation.component_ref == sub_or_key.component_ref,
        EvalObservation.target_revision.is_(None)
        if sub_or_key.target_revision is None
        else EvalObservation.target_revision == sub_or_key.target_revision,
        EvalObservation.probe == sub_or_key.probe,
        EvalObservation.probe_version == sub_or_key.probe_version,
        EvalObservation.privacy_scope == scope,
        EvalObservation.tenant_id.is_(None)
        if tenant_id is None
        else EvalObservation.tenant_id == tenant_id,
    )


def submit(
    session: Session,
    submission: Submission,
    *,
    privacy_scope: PrivacyScope = PrivacyScope.PUBLIC,
    tenant_id: uuid.UUID | None = None,
    promote: bool = True,
    now: datetime | None = None,
) -> Verdict:
    """Record one eval run and report what the pooled evidence now supports.

    `privacy_scope` and `tenant_id` are the caller's, never the submitter's. A runner reports
    what it observed; who may see it is decided by which credential it arrived on — the same
    split the extractor uses, and for the same reason: it keeps a privacy decision out of the
    path most exposed to untrusted input.
    """
    if (privacy_scope is PrivacyScope.TENANT) != (tenant_id is not None):
        raise ValueError("tenant scope requires a tenant, and a tenant requires tenant scope")
    if submission.runs <= 0:
        raise ValueError("a submission with no runs is not evidence")
    if submission.failures < 0 or submission.errors < 0:
        raise ValueError("counts cannot be negative")
    if submission.failures + submission.errors > submission.runs:
        raise ValueError("failures and errors cannot exceed runs")

    existing = session.execute(
        select(EvalObservation).where(
            EvalObservation.run_id == submission.run_id,
            EvalObservation.tenant_id.is_(None)
            if tenant_id is None
            else EvalObservation.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()

    accepted = existing is None
    if accepted:
        session.add(
            EvalObservation(
                run_id=submission.run_id,
                subject_domain=submission.subject_domain,
                component_ref=submission.component_ref,
                target_revision=submission.target_revision,
                probe=submission.probe,
                probe_version=submission.probe_version,
                prompt_digest=submission.prompt_digest,
                runs=submission.runs,
                failures=submission.failures,
                errors=submission.errors,
                samples=[s[:MAX_SAMPLE_CHARS] for s in submission.samples[:MAX_SAMPLES]],
                notes=submission.notes,
                privacy_scope=privacy_scope,
                tenant_id=tenant_id,
            )
        )
        session.flush()

    return _verdict(
        session,
        submission,
        privacy_scope=privacy_scope,
        tenant_id=tenant_id,
        accepted=accepted,
        promote=promote,
        now=now,
    )


def _verdict(
    session: Session,
    key,  # type: ignore[no-untyped-def]
    *,
    privacy_scope: PrivacyScope,
    tenant_id: uuid.UUID | None,
    accepted: bool,
    promote: bool,
    now: datetime | None,
) -> Verdict:
    totals = session.execute(
        select(
            func.coalesce(func.sum(EvalObservation.runs), 0),
            func.coalesce(func.sum(EvalObservation.failures), 0),
            func.coalesce(func.sum(EvalObservation.errors), 0),
        ).where(*_pool_filters(key, privacy_scope, tenant_id))
    ).one()
    pooled_runs, pooled_failures, pooled_errors = (int(x) for x in totals)

    # Behavioural failures over behavioural runs. A transport error says nothing about the
    # target, so it is excluded from both sides rather than counted as a pass or a failure.
    behavioural = pooled_runs - pooled_errors
    rate = wilson(pooled_failures, behavioural)

    record_id: uuid.UUID | None = None
    retracted = False
    if promote:
        record_id, retracted = _reconcile(
            session, key, rate, privacy_scope=privacy_scope, tenant_id=tenant_id, now=now
        )

    return Verdict(
        pooled_runs=pooled_runs,
        pooled_failures=pooled_failures,
        pooled_errors=pooled_errors,
        behavioural_runs=behavioural,
        rate=rate,
        accepted=accepted,
        record_id=record_id,
        retracted=retracted,
    )


def identity_for(key) -> str:  # type: ignore[no-untyped-def]
    """Stable identity for the record a pooling key would produce.

    Derived from the key rather than from the title, because the title carries the observed
    percentage and that moves with every submission. Identity keyed on a moving number would
    mint a new record per run — which is the failure the catalog's dedup exists to prevent,
    reappearing through a different door.
    """
    revision = key.target_revision or "unpinned"
    return f"eval:{key.component_ref}:{key.probe}@{key.probe_version}:{revision}"


def _reconcile(
    session: Session,
    key,  # type: ignore[no-untyped-def]
    rate: FailureRate,
    *,
    privacy_scope: PrivacyScope,
    tenant_id: uuid.UUID | None,
    now: datetime | None,
) -> tuple[uuid.UUID | None, bool]:
    """Bring the catalog into line with what the pooled evidence supports.

    Both directions. Promotion on corroboration is the obvious half; retraction on refutation is
    the half that is easy to leave out and expensive to leave out, because a record whose
    evidence has evaporated goes on grounding findings with a citation that says it should be
    believed.
    """
    identity = identity_for(key)
    existing = session.execute(
        select(BugRecord).where(BugRecord.identity_key == identity)
    ).scalar_one_or_none()

    if rate.corroborated:
        at = now or datetime.now(UTC)
        percent = f"{rate.observed * 100:.1f}%"
        interval = f"{rate.lower * 100:.1f} to {rate.upper * 100:.1f}%"
        result = write(
            session,
            IncomingBug(
                subject_domain=key.subject_domain,
                component_ref=key.component_ref,
                ecosystem=None,
                bug_type=BugType.FUNCTIONAL,
                applicability=_applicability(key, at),
                title=f"{key.component_ref} fails {key.probe} in {percent} of runs",
                description=(
                    f"Probe {key.probe} version {key.probe_version} failed "
                    f"{rate.failures} of {rate.runs} behavioural runs ({percent}); "
                    f"95% confidence interval {interval}. Corroborated because the lower "
                    f"bound exceeds the tolerated failure rate."
                ),
                identity_key=identity,
            ),
            origin=RecordOrigin.EVAL_DERIVED,
            privacy_scope=privacy_scope,
            tenant_id=tenant_id,
        )
        if existing is not None and existing.lifecycle_state is LifecycleState.RETRACTED:
            # Evidence can come back. A record retracted on one week's runs and re-corroborated
            # on the next is the normal life of a probabilistic defect, not an anomaly.
            existing.lifecycle_state = LifecycleState.ACTIVE
        return result.record_id, False

    if rate.refuted and existing is not None:
        if existing.lifecycle_state is not LifecycleState.RETRACTED:
            existing.lifecycle_state = LifecycleState.RETRACTED
            logger.info("retracted %s: evidence no longer supports it", identity)
            return existing.id, True
        return existing.id, False

    # Undetermined: neither shown nor shown absent. Deliberately nothing happens — writing a
    # record here would assert a defect the evidence does not support, and retracting one would
    # assert absence it equally does not support.
    return (existing.id if existing else None), False


def _applicability(key, at: datetime) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """The applicability shape this subject actually admits.

    A hosted model has no versions, so a version range cannot describe it — this is the case
    the tagged union exists for. Versioned software evaluated by the same mechanism gets a
    version range, because for it one does apply.
    """
    if key.subject_domain is SubjectDomain.LLM_MODEL:
        return {
            "kind": "model_revision",
            "model": key.component_ref,
            "revision": key.target_revision,
            "observed_at": at.isoformat(),
        }
    if key.target_revision:
        return {
            "kind": "version_range",
            "scheme": "generic",
            "introduced_in": key.target_revision,
            "fixed_in": None,
        }
    # Nothing to pin it to. A time window says what we actually know — that the behaviour was
    # present when we looked — rather than inventing a range we never established.
    return {"kind": "time_window", "observed_from": at.isoformat(), "observed_to": None}


def verdict_for(
    session: Session,
    key,  # type: ignore[no-untyped-def]
    *,
    privacy_scope: PrivacyScope = PrivacyScope.PUBLIC,
    tenant_id: uuid.UUID | None = None,
) -> Verdict:
    """Current pooled state for a key, without submitting anything.

    Exists so a runner can ask whether more runs are worth paying for before paying for them.
    Evals are the most expensive thing in this system by a wide margin.
    """
    return _verdict(
        session,
        key,
        privacy_scope=privacy_scope,
        tenant_id=tenant_id,
        accepted=False,
        promote=False,
        now=None,
    )
