"""Python dependency resolution.

Lockfiles first, always. A `pyproject.toml` or `requirements.txt` without pins gives declared
ranges, and a range cannot answer "which version am I actually running" — which is the only
question the catalog can match against.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from bugmine.inventory.models import Dependency

ECOSYSTEM = "pypi"

_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;#]+)")


def _from_uv_lock(path: Path) -> list[Dependency]:
    data = tomllib.loads(path.read_text())
    out = []
    for pkg in data.get("package", []):
        name, version = pkg.get("name"), pkg.get("version")
        if name and version:
            out.append(Dependency(ECOSYSTEM, name, version))
    return out


def _from_poetry_lock(path: Path) -> list[Dependency]:
    data = tomllib.loads(path.read_text())
    return [
        Dependency(ECOSYSTEM, p["name"], p.get("version"))
        for p in data.get("package", [])
        if p.get("name")
    ]


def _from_requirements(path: Path) -> list[Dependency]:
    out = []
    for line in path.read_text().splitlines():
        if line.lstrip().startswith(("#", "-")):
            continue
        m = _REQ.match(line)
        if m:
            out.append(Dependency(ECOSYSTEM, m.group(1), m.group(2)))
    return out


LOCKFILES = {
    "uv.lock": _from_uv_lock,
    "poetry.lock": _from_poetry_lock,
    "requirements.txt": _from_requirements,
}


def scan(root: Path) -> tuple[list[Dependency], list[str], list[str]]:
    deps: list[Dependency] = []
    sources: list[str] = []
    for filename, parser in LOCKFILES.items():
        path = root / filename
        if path.is_file():
            deps.extend(parser(path))
            sources.append(filename)

    unresolved: list[str] = []
    if not sources and (root / "pyproject.toml").is_file():
        unresolved.append("pyproject.toml (no lockfile)")
    return deps, sources, unresolved
