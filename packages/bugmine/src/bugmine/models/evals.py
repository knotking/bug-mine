"""Eval observations — evidence submitted by a probe runner, which is not yet a defect.

The distinction this table exists to hold: **an observation is evidence, a record is a claim.**
One eval run of forty attempts saying "four failed" does not establish a defect. Forty runs
across a week might. Nothing here writes to the catalog; promotion is a separate decision made
against pooled evidence in `bugmine.evals.intake`.

Runners live outside this system — a customer's CI, a scheduled suite, a partner's harness — so
this is an ingestion surface for claims about software we have no other way to observe. Two
properties follow from that and are enforced in the schema rather than in the caller:

**Submissions are idempotent.** A runner that retries after a timeout must not double-count.
Without `run_id` uniqueness a retry storm manufactures corroboration out of one bad afternoon,
and the resulting record would look exactly like a real finding.

**Evidence pools only within an identical question.** The pooling key includes the probe's
version and the target's revision. A probe whose prompt changed is asking something else, and
pooling its results with the old ones would let a change in what we asked look like a change in
how the target behaved.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow
from bugmine.models.enums import PrivacyScope, SubjectDomain
from bugmine.models.types import pg_enum


class EvalObservation(Base):
    """One submitted probe run against one target."""

    __tablename__ = "eval_observation"

    id: Mapped[uuid.UUID] = pk_uuid()

    run_id: Mapped[str] = mapped_column(String(120), nullable=False)
    """The runner's own identifier for this run. Uniqueness on it is what makes submission
    idempotent; a retry after a timeout carries the same one and is ignored."""

    subject_domain: Mapped[SubjectDomain] = mapped_column(
        pg_enum(SubjectDomain, "subject_domain"), nullable=False
    )
    component_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    target_revision: Mapped[str | None] = mapped_column(String(200))
    """What the provider said it served, when it says anything. Usually nothing for a hosted
    model, which is the entire reason that subject needs evals rather than a changelog."""

    probe: Mapped[str] = mapped_column(String(160), nullable=False)
    probe_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_digest: Mapped[str | None] = mapped_column(String(64))
    """Hash of the prompt as actually sent. An eval-derived record is the only kind asserting a
    defect nobody reported, so the claim rests on somebody else running the same thing."""

    runs: Mapped[int] = mapped_column(Integer, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Transport failures. Held apart from behavioural failures and excluded from the rate on
    both sides: a timeout is not the target being wrong, and counting it either way is a lie in
    one direction or the other."""

    samples: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    """A few failing responses, as evidence. Capped by the intake layer — a probe run a thousand
    times would otherwise carry a thousand copies of the same wrongness."""

    notes: Mapped[str | None] = mapped_column(Text)

    privacy_scope: Mapped[PrivacyScope] = mapped_column(
        pg_enum(PrivacyScope, "privacy_scope"), nullable=False, default=PrivacyScope.PUBLIC
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE")
    )

    submitted_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("run_id", "tenant_id", name="eval_observation_run_is_unique_per_owner"),
        # The pooling key. Every column in it is part of "the same question asked of the same
        # thing" — drop any one and results that answer different questions get summed.
        Index(
            "ix_eval_observation_pool",
            "subject_domain",
            "component_ref",
            "target_revision",
            "probe",
            "probe_version",
            "privacy_scope",
            "tenant_id",
        ),
        CheckConstraint(
            "(privacy_scope = 'tenant') = (tenant_id IS NOT NULL)",
            name="eval_observation_tenant_scope_requires_tenant",
        ),
        CheckConstraint("runs > 0", name="eval_observation_runs_positive"),
        CheckConstraint(
            "failures >= 0 AND errors >= 0 AND failures + errors <= runs",
            name="eval_observation_counts_are_consistent",
        ),
    )
