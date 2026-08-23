"""What a resolved dependency looks like locally."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dependency:
    ecosystem: str
    name: str
    version: str | None
    direct: bool = True

    def key(self) -> tuple[str, str]:
        return (self.ecosystem, self.name.lower())


@dataclass(frozen=True)
class Inventory:
    """The result of reading a project's lockfiles.

    `sources` records which files were actually read. A caller needs it to tell an empty
    project from an unrecognised one — the same distinction the catalog draws between "no
    findings" and "not covered", applied one layer earlier.
    """

    dependencies: tuple[Dependency, ...]
    sources: tuple[str, ...]
    unresolved: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """False when a manifest was found without a lockfile beside it.

        Scanning an unlocked project yields declared ranges rather than resolved versions, so
        matches against it are inherently unconfirmed. Reporting that is the difference between
        a partial answer and a wrong one.
        """
        return not self.unresolved
