"""Turning a pulled image into the list of what is installed in the filesystem that runs.

One image carries several ecosystems at once, which a repository scan never sees: OS packages
plus whatever the application brought with it. They are inventoried together and kept
distinguishable, because `openssl` the Debian package and `openssl` the pypi wheel are different
components with different advisories.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass

from bugmine.container import packages
from bugmine.container.layers import FlattenedImage, flatten, layer_entries
from bugmine.container.packages import APK_DB, DPKG_DB, RPM_DIRS
from bugmine.container.registry import PulledImage
from bugmine.inventory.models import Dependency

PYTHON_METADATA = "dist-info/METADATA"
NPM_MANIFEST = "package.json"


def wanted(path: str) -> bool:
    """Which files are worth reading out of a multi-gigabyte image.

    The predicate is the reason a container scan is affordable at all: package databases and
    manifests are megabytes, the filesystem around them is not.
    """
    if path in (APK_DB, DPKG_DB):
        return True
    if any(path.startswith(d) for d in RPM_DIRS):
        return True
    if path.endswith(PYTHON_METADATA):
        return True
    if path.endswith("/" + NPM_MANIFEST) or path == NPM_MANIFEST:
        # Only inside node_modules. An application's own package.json is not a dependency, and
        # counting it would file findings against the thing being scanned.
        return "node_modules/" in path
    return False


@dataclass(frozen=True)
class ImageInventory:
    dependencies: tuple[Dependency, ...]
    sources: tuple[str, ...]
    """Which databases were actually read."""
    deleted_paths: int = 0
    """How many files later layers removed. The evidence behind any suppression."""
    truncated: bool = False
    """A cap stopped the read. Must reach the user: a partial inventory looks exactly like a
    small image, and reports as cleanly."""

    @property
    def complete(self) -> bool:
        """Whether this inventory can be reported on as if it were the whole image."""
        return not self.truncated and bool(self.sources)


def flatten_image(image: PulledImage) -> FlattenedImage:
    return flatten(layer_entries(layer, wanted) for layer in image.layers)


def read(flattened: FlattenedImage) -> ImageInventory:
    """Inventory a flattened image.

    Note what this operates on: the *flattened* filesystem, so a package installed in an early
    layer and removed in a later one is already gone. That narrowing happens before matching
    rather than after, which is what keeps it from being a suppression the user has to trust.
    """
    found: list[Dependency] = []
    sources: list[str] = []

    for path, blob in sorted(flattened.files.items()):
        text: str | None = None

        if path == APK_DB:
            text = _decode(blob)
            if text is not None:
                found.extend(packages.parse_apk(text))
                sources.append(path)
        elif path == DPKG_DB:
            text = _decode(blob)
            if text is not None:
                found.extend(packages.parse_dpkg(text))
                sources.append(path)
        elif path.endswith(PYTHON_METADATA):
            text = _decode(blob)
            if text is not None and (dep := packages.parse_python_metadata(text)):
                found.append(dep)
                sources.append(path)
        elif "node_modules/" in path and posixpath.basename(path) == NPM_MANIFEST:
            try:
                payload = json.loads(blob)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict) and (dep := packages.parse_npm_package_json(payload)):
                found.append(dep)
                sources.append(path)

    return ImageInventory(
        dependencies=_deduplicate(found),
        sources=tuple(sources),
        deleted_paths=len(flattened.deleted),
        truncated=flattened.truncated,
    )


def _decode(blob: bytes) -> str | None:
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        # Latin-1 rather than errors="replace": a mangled version string matches nothing and
        # reports the package as absent, which is the failure direction that reads as clean.
        try:
            return blob.decode("latin-1")
        except UnicodeDecodeError:
            return None


def _deduplicate(found: list[Dependency]) -> tuple[Dependency, ...]:
    """One entry per (ecosystem, name, version).

    A layer can carry the same wheel in two paths, and reporting it twice would double every
    finding against it — which reads to a user as two problems rather than one.
    """
    seen: dict[tuple[str, str, str | None], Dependency] = {}
    for dep in found:
        seen.setdefault((dep.ecosystem, dep.name.lower(), dep.version), dep)
    return tuple(seen.values())
