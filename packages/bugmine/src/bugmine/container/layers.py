"""Flattening image layers into the filesystem that actually runs.

An image is an ordered stack of tar archives. Later layers add files, replace files, and — this
is the part that matters — **delete** them, using marker entries rather than by removing
anything. A package installed in layer 2 and removed in layer 6 is present in the union of the
layers and absent from the running container.

Reporting on the union is the container-scanning equivalent of reporting a dependency your code
never calls: technically it was there, and acting on it is wasted work. Worse here, because the
user cannot act at all — they would go looking for a package that is not in the image.

Two marker conventions, and both must be handled:

- `.wh.<name>` beside a path deletes that entry.
- `.wh..wh..opq` inside a directory deletes *everything inherited* into that directory from
  lower layers, while keeping what this layer puts there. Missing it leaves a whole tree of
  deleted files visible — and it is the one people forget, because it appears rarely and only
  in images built by tools that use it.

Deliberately selective. A real image is gigabytes and we need the package databases, not the
filesystem, so callers pass a predicate and everything else is skipped without being read.
"""

from __future__ import annotations

import posixpath
import tarfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

WHITEOUT_PREFIX = ".wh."
OPAQUE_MARKER = ".wh..wh..opq"

MAX_MEMBER_BYTES = 8 * 1024 * 1024
"""Per-file cap. Package databases are small; anything larger is not one, and reading it would
let a crafted image exhaust the worker's memory."""

MAX_TOTAL_BYTES = 96 * 1024 * 1024
"""Across every layer of one image."""


@dataclass
class FlattenedImage:
    files: dict[str, bytes] = field(default_factory=dict)
    """Path to contents, as the running container would see it."""
    deleted: set[str] = field(default_factory=set)
    """Paths a later layer removed. Kept rather than discarded because "this package was
    deleted in a later layer" is the reason a finding was suppressed, and a suppression with no
    reason is indistinguishable from a gap in coverage."""
    truncated: bool = False
    """Whether a cap stopped us reading everything we wanted. Must reach the user: a partial
    read looks exactly like a small image."""


def _normalise(name: str) -> str:
    """Tar entries arrive as `./usr/bin/x`, `/usr/bin/x`, or `usr/bin/x` from different builders.

    Left unnormalised these are three different keys, so a later layer's `./etc/passwd` fails to
    overwrite an earlier layer's `etc/passwd` and both survive into the result.
    """
    cleaned = posixpath.normpath(name.lstrip("/"))
    return "" if cleaned in (".", "..") else cleaned


def _is_unsafe(path: str) -> bool:
    """Reject traversal. We never write these to disk, but a path escaping the image root would
    corrupt the flattened map — `../../etc/x` from one layer aliasing a real entry."""
    return path.startswith("../") or path == ".." or path.startswith("/")


def flatten(
    layers: Iterable[Iterable[tuple[str, Callable[[], bytes] | None, bool]]],
) -> FlattenedImage:
    """Apply layers in order, oldest first.

    Each layer yields `(path, read, is_dir)`. `read` is a thunk so a caller can skip the
    contents of files it does not want — which is the difference between reading a package
    database and reading a gigabyte of application code.
    """
    image = FlattenedImage()
    total = 0

    for layer in layers:
        opaque_dirs: list[str] = []
        additions: dict[str, bytes] = {}
        removals: list[str] = []

        for raw, read, is_dir in layer:
            path = _normalise(raw)
            if not path or _is_unsafe(path):
                continue

            base = posixpath.basename(path)
            if base == OPAQUE_MARKER:
                opaque_dirs.append(posixpath.dirname(path))
                continue
            if base.startswith(WHITEOUT_PREFIX):
                target = posixpath.join(posixpath.dirname(path), base[len(WHITEOUT_PREFIX) :])
                removals.append(target)
                continue
            if is_dir or read is None:
                continue

            data = read()
            if len(data) > MAX_MEMBER_BYTES:
                image.truncated = True
                continue
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                image.truncated = True
                break
            additions[path] = data

        # Deletions are applied before this layer's own additions. A layer may whiteout a path
        # and immediately write a new file there — a replacement, not a removal — and applying
        # them the other way round would delete the replacement.
        for directory in opaque_dirs:
            prefix = f"{directory}/" if directory else ""
            for existing in [p for p in image.files if p.startswith(prefix)]:
                del image.files[existing]
                image.deleted.add(existing)
        for target in removals:
            prefix = f"{target}/"
            for existing in [
                p for p in image.files if p == target or p.startswith(prefix)
            ]:
                del image.files[existing]
                image.deleted.add(existing)

        for path, data in additions.items():
            image.files[path] = data
            # Written again after being deleted: it is present, so it is not deleted.
            image.deleted.discard(path)

    return image


def layer_entries(
    tar: tarfile.TarFile, wanted: Callable[[str], bool]
) -> list[tuple[str, Callable[[], bytes] | None, bool]]:
    """Adapt a tar archive to what `flatten` consumes, reading only what `wanted` accepts.

    Whiteout markers are always yielded regardless of the predicate. They are how deletion is
    recorded, and skipping them because the deleted file was uninteresting would leave the
    deletion unrecorded — reinstating exactly the union-of-layers error.
    """
    out: list[tuple[str, Callable[[], bytes] | None, bool]] = []
    for member in tar.getmembers():
        base = posixpath.basename(member.name)
        marker = base.startswith(WHITEOUT_PREFIX)
        if not marker and not member.isfile():
            out.append((member.name, None, member.isdir()))
            continue
        if marker:
            out.append((member.name, None, False))
            continue
        if not wanted(_normalise(member.name)):
            continue

        def read(m: tarfile.TarInfo = member) -> bytes:
            handle = tar.extractfile(m)
            return handle.read(MAX_MEMBER_BYTES + 1) if handle else b""

        out.append((member.name, read, False))
    return out
