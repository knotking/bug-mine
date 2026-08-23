"""Wire types.

Deliberately distinct from the ORM models. The stored shape and the wire shape evolve at
different speeds, and coupling them is how a column rename becomes a breaking API change.

Omitted on purpose: `content_hash` (an internal dedup mechanism) and `identity_key` (whose
resolution strategy is unsolved — exposing it would freeze an unsolved design into a public
contract).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from bugmine.models import BugType, LifecycleState, PrivacyScope, RecordOrigin, SubjectDomain


class ComponentOut(BaseModel):
    ref: str
    subject_domain: SubjectDomain
    ecosystem: str | None = None


class CitationOut(BaseModel):
    """The edge that separates a grounded finding from an assertion."""

    bug_version_id: str
    bug_id: str
    evidence_url: str | None = None


class BugOut(BaseModel):
    id: str
    scope: PrivacyScope
    origin: RecordOrigin
    lifecycle_state: LifecycleState
    bug_type: BugType
    subject_domain: SubjectDomain
    component: ComponentOut
    applicability: dict[str, Any]
    title: str
    description: str | None = None
    evidence_url: str | None = None
    observed_at: datetime


class DependencyIn(BaseModel):
    ecosystem: str
    name: str
    version: str | None = Field(
        default=None,
        description="Opaque. Never normalised by the API — version semantics are per-ecosystem.",
    )
    direct: bool = True


class DependencyCheckIn(BaseModel):
    dependencies: list[DependencyIn] = Field(min_length=1, max_length=2000)
    bug_types: list[BugType] | None = None


class DependencyMatchOut(BaseModel):
    dependency: DependencyIn
    bug_id: str
    bug_type: BugType
    title: str
    version_confirmed: bool = Field(
        description="False when the record matched only because the version was unknown."
    )
    match_note: str | None = None
    citation: CitationOut


class NotCoveredOut(BaseModel):
    ecosystem: str
    name: str
    version: str | None
    reason: Literal["component_not_in_catalog", "ecosystem_unsupported", "unknown"]


class DependencyCheckOut(BaseModel):
    matches: list[DependencyMatchOut]
    not_covered: list[NotCoveredOut] = Field(
        description="Reported explicitly — silence would read as a clean bill of health."
    )
    checked_at: datetime


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorBody
