"""Applicability — when a catalog record applies to a component.

The decision recorded in `docs/data-model/stack-profile.md` §2: **applicability is a tagged
union, not a version range with special cases bolted on.** Three of the seven subject domains
have no versions in the semver sense:

- A database, queue, language or library ships versions; ``affects 7.0 - 7.2.3`` is meaningful.
- An operating system ships builds that only partially order.
- A SaaS platform ships you nothing at all. Twilio's behavior changes on a *date*.
- An LLM model is an identifier plus, often, a date when behavior shifted without the
  identifier changing.

A schema with ``version_min``/``version_max`` meets Twilio in week two and grows a nullable
date pair that means something different per row. Hence this union.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Version comparison schemes
# ---------------------------------------------------------------------------


class VersionScheme(BaseModel):
    """Marker for how two version strings should be ordered."""


def _parse(value: str, scheme: str) -> Version | None:
    """Parse a version string, returning None when it cannot be ordered.

    An unparseable version is *not* an error. It means we cannot make a claim about ordering,
    and the caller must treat that as "cannot exclude" rather than "does not match" — the
    failure direction ADR-0006 specifies for reachability applies here too.
    """
    try:
        return Version(value)
    except InvalidVersion:
        return None


# ---------------------------------------------------------------------------
# The four variants
# ---------------------------------------------------------------------------


class VersionRange(BaseModel):
    """Databases, queues, languages, repos — things that ship ordered versions."""

    kind: Literal["version_range"] = "version_range"
    scheme: Literal["semver", "pep440", "maven", "generic"] = "generic"
    introduced_in: str | None = None
    """First affected version. None means "affected from the beginning"."""
    fixed_in: str | None = None
    """First version containing the fix. None means "no fix known"."""

    def matches(self, version: str | None) -> MatchResult:
        if version is None:
            return MatchResult.unconfirmed("component version unknown")

        target = _parse(version, self.scheme)
        if target is None:
            return MatchResult.unconfirmed(f"cannot order version {version!r}")

        if self.introduced_in is not None:
            lower = _parse(self.introduced_in, self.scheme)
            if lower is None:
                return MatchResult.unconfirmed("record's introduced_in is unorderable")
            if target < lower:
                return MatchResult.no("predates the affected range")

        if self.fixed_in is not None:
            upper = _parse(self.fixed_in, self.scheme)
            if upper is None:
                return MatchResult.unconfirmed("record's fixed_in is unorderable")
            if target >= upper:
                return MatchResult.no("at or past the fixed version")

        return MatchResult.yes()


class BuildRange(BaseModel):
    """Operating systems — build and kernel identifiers that only partially order."""

    kind: Literal["build_range"] = "build_range"
    builds: list[str] = Field(default_factory=list)
    """Explicit affected build identifiers. Set membership, not comparison."""

    def matches(self, version: str | None) -> MatchResult:
        if version is None:
            return MatchResult.unconfirmed("build identifier unknown")
        if version in self.builds:
            return MatchResult.yes()
        return MatchResult.no("build not in the affected set")


class TimeWindow(BaseModel):
    """SaaS platforms — no version exists; behavior changed on a date."""

    kind: Literal["time_window"] = "time_window"
    observed_from: datetime
    observed_until: datetime | None = None
    """None means ongoing."""

    def matches(self, version: str | None, *, at: datetime | None = None) -> MatchResult:
        # `version` is ignored by design: a SaaS platform ships the caller no version.
        if at is None:
            return MatchResult.unconfirmed("no evaluation time supplied")
        if at < self.observed_from:
            return MatchResult.no("before the affected window")
        if self.observed_until is not None and at > self.observed_until:
            return MatchResult.no("after the affected window")
        return MatchResult.yes()


class ModelRevision(BaseModel):
    """LLM models — an identifier, plus optionally a date behavior shifted under it.

    The identifier alone is insufficient because a floating alias repoints without changing,
    which is the whole reason evals exist as an origin.
    """

    kind: Literal["model_revision"] = "model_revision"
    model_id: str
    effective_from: datetime | None = None

    def matches(self, version: str | None, *, at: datetime | None = None) -> MatchResult:
        if version is None:
            return MatchResult.unconfirmed("model identifier unknown")
        if version != self.model_id:
            return MatchResult.no("different model identifier")
        if self.effective_from is None:
            return MatchResult.yes()
        if at is None:
            return MatchResult.unconfirmed("behavior is date-bounded but no time supplied")
        if at < self.effective_from:
            return MatchResult.no("before the behavior change")
        return MatchResult.yes()


Applicability = Annotated[
    VersionRange | BuildRange | TimeWindow | ModelRevision,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Match results
# ---------------------------------------------------------------------------


class MatchResult(BaseModel):
    """Three-valued, deliberately.

    ``UNCONFIRMED`` is not a soft ``NO``. It means the record may well apply but we lack the
    fact needed to decide — an unknown version, an unorderable string, a missing evaluation
    date. Collapsing it into either boolean is the failure `docs/requirements/advisor.md`
    FR-26 forbids: presenting "we cannot tell" as "nothing found".
    """

    matched: bool
    confirmed: bool
    reason: str | None = None

    @classmethod
    def yes(cls) -> MatchResult:
        return cls(matched=True, confirmed=True)

    @classmethod
    def no(cls, reason: str) -> MatchResult:
        return cls(matched=False, confirmed=True, reason=reason)

    @classmethod
    def unconfirmed(cls, reason: str) -> MatchResult:
        """Widen to a match, but flag that it was never confirmed.

        A finding built on an unconfirmed match must never be presented as a confirmed one.
        """
        return cls(matched=True, confirmed=False, reason=reason)
