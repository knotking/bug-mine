"""Local dependency inventory.

This module is why source code never leaves the developer's machine. The dependency graph is
resolved here, and only component names and versions are sent to the API — which is a set of
facts a lockfile already publishes.
"""

from __future__ import annotations

from pathlib import Path

from bugmine.inventory import npm, python
from bugmine.inventory.models import Dependency, Inventory

__all__ = ["Dependency", "Inventory", "collect"]

_SCANNERS = (python.scan, npm.scan)


def collect(root: Path) -> Inventory:
    deps: list[Dependency] = []
    sources: list[str] = []
    unresolved: list[str] = []

    for scan in _SCANNERS:
        d, s, u = scan(root)
        deps.extend(d)
        sources.extend(s)
        unresolved.extend(u)

    # Deduplicate: several lockfiles can name the same package at the same version.
    seen: dict[tuple[str, str, str | None], Dependency] = {}
    for dep in deps:
        seen.setdefault((dep.ecosystem, dep.name.lower(), dep.version), dep)

    return Inventory(
        dependencies=tuple(sorted(seen.values(), key=lambda d: (d.ecosystem, d.name.lower()))),
        sources=tuple(sources),
        unresolved=tuple(unresolved),
    )
