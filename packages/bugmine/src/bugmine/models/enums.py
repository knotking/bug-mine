"""Enumerations shared across the catalog.

The two axes of `docs/requirements/bug-taxonomy.md` are `SubjectDomain` (what software the
bug is in) and `BugType` (what kind of defect it is). Both are required on every record:
either alone is unreachable by one of the two retrieval paths.
"""

from enum import StrEnum


class SubjectDomain(StrEnum):
    """Axis 1 — what software the defect lives in."""

    LLM_MODEL = "llm_model"
    OPERATING_SYSTEM = "operating_system"
    DATABASE = "database"
    MESSAGING_QUEUE = "messaging_queue"
    SAAS_PLATFORM = "saas_platform"
    LANGUAGE_RUNTIME = "language_runtime"
    REPO_LIBRARY = "repo_library"


class BugType(StrEnum):
    """Axis 2 — what kind of defect it is."""

    SECURITY = "security"
    FUNCTIONAL = "functional"
    PERFORMANCE = "performance"
    COMPATIBILITY = "compatibility"
    BREAKING_CHANGE = "breaking_change"
    DEPRECATION = "deprecation"
    BUILD = "build"


class RecordOrigin(StrEnum):
    """FR-40 — how the record entered the catalog.

    Visible on the record and on every finding citing it: a user weighing a finding needs to
    know whether it rests on a vendor advisory, on observation across customers, or on a
    reproducible probe. These are different kinds of claim.
    """

    CRAWLED = "crawled"
    SCAN_DERIVED = "scan_derived"
    EVAL_DERIVED = "eval_derived"


class LifecycleState(StrEnum):
    """FR-64. See `docs/requirements/record-lifecycle.md`.

    Note that FIXED still grounds findings: a fix does not erase applicability, it adds a
    resolution. Being on an old version is the normal condition.
    """

    CANDIDATE = "candidate"
    ACTIVE = "active"
    FIXED = "fixed"
    DISPUTED = "disputed"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"

    @property
    def grounds_findings(self) -> bool:
        """Whether a record in this state may ground a *new* finding."""
        return self in {
            LifecycleState.ACTIVE,
            LifecycleState.FIXED,
            LifecycleState.DISPUTED,
        }


class JobType(StrEnum):
    """One substrate, many job types. Adding a capability adds a member here."""

    NOOP = "noop"
    CRAWL = "crawl"
    EXTRACT = "extract"
    SCAN_FETCH = "scan_fetch"
    SCAN_ANALYZE = "scan_analyze"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"

    @property
    def terminal(self) -> bool:
        return self in {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.BUDGET_EXCEEDED,
        }
