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


class CrawlTriggerIn(BaseModel):
    url: str
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY
    component_ref: str | None = None
    ecosystem: str | None = None

class ExtractTriggerIn(BaseModel):
    """Re-run extraction over an artifact already in storage.

    Needed because the crawl worker chains extraction only when content changed, which is the
    right default — an unchanged source should cost no tokens. But it means a fix to the
    extractor cannot be applied to what has already been crawled: re-crawling returns the same
    bytes, reports no change, and enqueues nothing. This is the way back in.
    """

    artifact_uri: str
    component_ref: str | None = None
    ecosystem: str | None = None
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY
    mode: Literal["auto", "structured", "llm"] = "auto"



class JobHandleOut(BaseModel):
    task: str
    queued: bool


class TenantOut(BaseModel):
    id: str
    name: str
    slug: str


class MemberOut(BaseModel):
    email: str
    role: str
    joined_at: datetime


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    principal_kind: str
    created_at: datetime
    revoked: bool


class UsageLineOut(BaseModel):
    """One row of the usage breakdown."""

    label: str
    purpose: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    calls: int
    failed_calls: int
    cost_micros: int


class UsageOut(BaseModel):
    """Token and cost accounting for the calling tenant.

    Deliberately scoped to the tenant. System spend — the cost of crawling the world so the
    catalog exists — is not a tenant's to see: it does not vary with their usage and exposing
    it would invite reading it as a bill.
    """

    since: datetime
    until: datetime
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_micros: int
    by_team: list[UsageLineOut]
    by_user: list[UsageLineOut]
    by_purpose: list[UsageLineOut]


class StartScanIn(BaseModel):
    repo_url: str
    ref: str | None = None


class ScanOut(BaseModel):
    id: str
    repo_ref: str
    commit_sha: str | None = None
    created_at: datetime
    grounding_complete: bool
    uncovered_components: int
    unresolved_manifests: int = 0
    suppressed_unreachable: int = 0
    """Matches narrowed away because the code does not touch them. A scan that suppressed
    everything is otherwise indistinguishable from one that found nothing."""
    dependencies_scanned: int = 0
    """Dependencies declared but not pinned. A scan with findings=0 and this above zero did not
    read the dependency graph at all, which is not the same as finding nothing."""
    findings: int


class FindingOut(BaseModel):
    id: str
    component: str
    bug_type: BugType
    title: str
    detail: str | None = None
    reachable: bool | None = None
    """None means undetermined — reported with reduced confidence, never suppressed. A stored
    False would be a claim we never make: a confirmed negative produces no finding at all."""
    confidence: float
    version_confirmed: bool
    citations: list[str]


class StackComponentIn(BaseModel):
    ref: str
    version: str | None = None
    ecosystem: str | None = None
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY


class AdviseIn(BaseModel):
    components: list[StackComponentIn] = Field(default_factory=list)
    intention: str | None = None
    expected_scale: str | None = None


class AdviceOut(BaseModel):
    grounded: list[dict[str, Any]]
    """Findings from catalog records, each cited."""
    unknowns: list[str]
    """Specific missing facts. Non-empty with no findings means "not enough information to say",
    which is a different answer from "no known problems" and must not be read as one."""
    not_covered: list[str]
    interactions: list[dict[str, Any]]
    has_enough_to_say: bool


class QuotaOut(BaseModel):
    id: str
    scope: Literal["tenant", "team", "user"]
    team_id: str | None = None
    user_id: str | None = None
    label: str
    limit_micros: int | None = None
    limit_tokens: int | None = None
    spent_micros: int = 0
    spent_tokens: int = 0


class SetQuotaIn(BaseModel):
    team_id: str | None = None
    user_id: str | None = None
    """Both null sets the tenant-wide limit. At most one may be given — a limit narrowing to a
    team and a user at once has no meaning under most-specific-wins."""
    limit_micros: int | None = Field(default=None, ge=0)
    limit_tokens: int | None = Field(default=None, ge=0)


class MintKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    team_id: str | None = None
    user_id: str | None = None
    """Exactly one of team_id or user_id. FR-80: a key is never tenant-wide, because the token
    ledger attributes spend per team and per user and a shared key cannot populate either."""


class MintedKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    secret: str
    """Returned once, at creation, and never retrievable again — only the hash is stored."""


class SourceOut(BaseModel):
    id: str
    url: str
    subject_domain: SubjectDomain
    component_ref: str | None
    ecosystem: str | None
    scope: PrivacyScope
    interval_minutes: int
    enabled: bool
    last_success_at: datetime | None
    consecutive_failures: int
    stale: bool = Field(
        description="No successful run in several intervals. A dead source produces no errors."
    )


class SourceIn(BaseModel):
    url: str
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY
    component_ref: str | None = None
    ecosystem: str | None = None
    interval_minutes: int = Field(default=1440, ge=15)


class WhoAmIOut(BaseModel):
    tenant: TenantOut
    principal_kind: str
    principal_id: str
