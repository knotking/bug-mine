"""Job state — one table for every job type on the shared substrate (FR-6)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bugmine.models.base import Base, pk_uuid, tz_timestamp, utcnow
from bugmine.models.enums import JobState, JobType
from bugmine.models.types import pg_enum


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = pk_uuid()
    job_type: Mapped[JobType] = mapped_column(pg_enum(JobType, "job_type"), nullable=False)
    state: Mapped[JobState] = mapped_column(
        pg_enum(JobState, "job_state"), nullable=False, default=JobState.QUEUED
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tenant_id: Mapped[str | None] = mapped_column(String(100), index=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    """Distinguishes failure from in-progress and success (FR-6), and a budget stop from a
    bug — a user hitting a ceiling needs to know it was a limit, not a crash."""

    budget_tokens: Mapped[int | None] = mapped_column(Integer)
    """The reservation. NFR-40: overrun fails the job rather than charging silently."""
    spent_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = tz_timestamp(nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = tz_timestamp()
    finished_at: Mapped[datetime | None] = tz_timestamp()

    __table_args__ = (
        Index("ix_job_type_state", "job_type", "state"),
        Index("ix_job_created", "created_at"),
    )
