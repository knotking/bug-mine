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
    omitted: dict[str, int] = Field(default_factory=dict)
    """Records held back per component by the relevance cap. A short list that looks complete is
    worse than a long one, so what was dropped is always stated."""
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


class EvalObservationIn(BaseModel):
    """One eval run, as a probe runner reports it.

    Counts rather than raw transcripts: BugMine needs to know how often the target misbehaved,
    not what it said each time. A handful of failing samples come along as evidence and are
    capped on arrival.
    """

    run_id: str = Field(
        max_length=120,
        description="The runner's own id for this run. Resubmitting it is a no-op, so a retry "
        "after a timeout cannot double-count.",
    )
    component_ref: str = Field(max_length=200, description="The target, e.g. gemini-3.7-flash")
    probe: str = Field(max_length=160)
    probe_version: str = Field(
        max_length=40,
        description="Bumped whenever the prompt or check changes. Results from different "
        "versions are never pooled — they answer different questions.",
    )
    runs: int = Field(gt=0, le=1_000_000)
    failures: int = Field(ge=0)
    errors: int = Field(
        default=0,
        ge=0,
        description="Transport failures. Excluded from the rate on both sides: a timeout is "
        "not the target being wrong.",
    )
    subject_domain: SubjectDomain = SubjectDomain.LLM_MODEL
    target_revision: str | None = Field(default=None, max_length=200)
    prompt_digest: str | None = Field(default=None, max_length=64)
    samples: list[str] = Field(default_factory=list, description="Failing responses; first 3 kept.")
    notes: str | None = None


class EvalVerdictOut(BaseModel):
    """What the pooled evidence supports — never what a single submission looks like."""

    accepted: bool = Field(
        description="False when this run_id had already been submitted. Not an error: the "
        "correct answer to a retry is the same answer, not a second count."
    )
    state: str = Field(description="corroborated | refuted | undetermined")
    pooled_runs: int
    pooled_failures: int
    pooled_errors: int
    behavioural_runs: int = Field(description="Pooled runs less transport errors.")
    observed_rate: float
    lower_bound: float = Field(
        description="Lower bound of the 95% Wilson interval. The number decisions are made on."
    )
    upper_bound: float
    runs_to_decide: int = Field(
        description="Roughly how many total runs would settle a defect at the observed rate. "
        "0 when already decided. Returned so a runner can schedule rather than guess."
    )
    record_id: str | None = Field(
        default=None, description="The catalog record, once the evidence supports one."
    )
    retracted: bool = Field(
        default=False,
        description="The record was withdrawn: accumulated evidence pushed the interval back "
        "below the tolerated rate.",
    )


class WhoAmIOut(BaseModel):
    tenant: TenantOut
    principal_kind: str
    principal_id: str
