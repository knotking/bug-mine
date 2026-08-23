"""npm dependency resolution."""

from __future__ import annotations

import json
from pathlib import Path

from bugmine.inventory.models import Dependency

ECOSYSTEM = "npm"


def _from_package_lock(path: Path) -> list[Dependency]:
    data = json.loads(path.read_text())
    out: list[Dependency] = []

    # lockfileVersion 2/3 keep everything under "packages", keyed by install path.
    for install_path, meta in data.get("packages", {}).items():
        if not install_path:  # the root project itself
            continue
        name = meta.get("name") or install_path.split("node_modules/")[-1]
        version = meta.get("version")
        if name and version:
            out.append(
                Dependency(ECOSYSTEM, name, version, direct=install_path.count("node_modules") == 1)
            )

    if out:
        return out

    # lockfileVersion 1 uses a nested "dependencies" tree.
    def walk(node: dict, depth: int = 0) -> None:
        for name, meta in (node.get("dependencies") or {}).items():
            if version := meta.get("version"):
                out.append(Dependency(ECOSYSTEM, name, version, direct=depth == 0))
            walk(meta, depth + 1)

    walk(data)
    return out


LOCKFILES = {"package-lock.json": _from_package_lock}


def scan(root: Path) -> tuple[list[Dependency], list[str], list[str]]:
    deps: list[Dependency] = []
    sources: list[str] = []
    for filename, parser in LOCKFILES.items():
        path = root / filename
        if path.is_file():
            deps.extend(parser(path))
            sources.append(filename)

    unresolved: list[str] = []
    if not sources and (root / "package.json").is_file():
        unresolved.append("package.json (no lockfile)")
    return deps, sources, unresolved
