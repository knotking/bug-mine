"""The three-valued reachability answer.

Shaped like `MatchResult` in the applicability model, for the same reason: "we cannot tell" is
not a soft no. Collapsing it into either boolean is the failure `docs/requirements/advisor.md`
FR-26 forbids — presenting "we could not determine this" as "nothing found".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Evidence:
    """Where the affected code is referenced. A verdict without this is an assertion."""

    path: str
    line: int
    symbol: str
    """The fully-qualified reference as resolved, e.g. "pydantic.BaseModel" — not the local
    alias, so a reader can match it against the record without re-reading the import."""


@dataclass(frozen=True)
class Reach:
    reached: bool
    """Whether the defect may touch this project. Widened on uncertainty."""

    confirmed: bool
    """Whether the analysis could actually decide. False means `reached` was widened rather
    than determined, and a finding built on it must not be presented as confirmed."""

    reason: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    @property
    def suppressible(self) -> bool:
        """Only a confirmed negative may hide a finding."""
        return not self.reached and self.confirmed

    @classmethod
    def reaches(cls, evidence: tuple[Evidence, ...], reason: str = "symbol is referenced") -> Reach:
        return cls(reached=True, confirmed=True, reason=reason, evidence=evidence)

    @classmethod
    def does_not_reach(cls, reason: str) -> Reach:
        """A positive determination that the project never touches the affected code.

        The only verdict that suppresses, so it is only ever returned when the source parsed
        cleanly and the reference genuinely is not there.
        """
        return cls(reached=False, confirmed=True, reason=reason)

    @classmethod
    def unknown(cls, reason: str) -> Reach:
        """Could not decide — so the finding is reported, and marked unconfirmed.

        Unparseable source, a record that names no symbol, an unsupported language. Each is a
        gap in our analysis, not evidence about the user's code, and answering "not reached"
        would turn our own limitation into their false assurance.
        """
        return cls(reached=True, confirmed=False, reason=reason)
