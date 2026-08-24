"""Parsing an image reference, which is harder than it looks and fails silently when wrong.

`python:3.12` and `python` and `docker.io/library/python:3.12` and
`myregistry.io:5000/team/app@sha256:…` are all valid, and the defaults that fill in the missing
parts are conventions rather than syntax. Getting them wrong does not raise — it pulls a
different image, or the right image from the wrong place, and the scan reports on something the
user never asked about.

The two that catch people:

**A host is only a host if it looks like one.** `myteam/app` is a Docker Hub repository owned by
`myteam`; `localhost/app` and `registry.io/app` are hosts. The rule the ecosystem settled on is
that the first segment is a registry if it contains a dot or a colon, or is exactly `localhost`.

**A bare name is `library/`-prefixed, but only on Docker Hub.** `python` means
`docker.io/library/python`. On any other registry it means what it says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_REGISTRY = "docker.io"
DEFAULT_TAG = "latest"
OFFICIAL_NAMESPACE = "library"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TAG = re.compile(r"^[\w][\w.-]{0,127}$")


@dataclass(frozen=True)
class ImageReference:
    registry: str
    repository: str
    tag: str | None
    digest: str | None

    @property
    def pinned(self) -> bool:
        """Whether this reference names exactly one artifact, forever.

        A tag does not. `python:3.12` resolves to a different image week to week, which is the
        whole reason base images need watching rather than scanning once — so whether a
        reference is pinned is a property worth reporting, not an implementation detail.
        """
        return self.digest is not None

    @property
    def canonical(self) -> str:
        suffix = f"@{self.digest}" if self.digest else f":{self.tag}"
        return f"{self.registry}/{self.repository}{suffix}"

    @property
    def api_host(self) -> str:
        """Docker Hub's registry API is not served from docker.io."""
        return "registry-1.docker.io" if self.registry == DEFAULT_REGISTRY else self.registry


def _looks_like_a_registry(segment: str) -> bool:
    return "." in segment or ":" in segment or segment == "localhost"


def parse(reference: str) -> ImageReference:
    """Parse an image reference, applying the ecosystem's defaults.

    Raises on anything ambiguous rather than guessing. A reference we cannot parse confidently
    is one we would pull from somewhere the user did not intend.
    """
    text = reference.strip()
    if not text:
        raise ValueError("empty image reference")

    digest: str | None = None
    if "@" in text:
        text, _, digest = text.partition("@")
        if not _DIGEST.match(digest):
            raise ValueError(f"unsupported digest: {digest!r}")

    head, _, rest = text.partition("/")
    if rest and _looks_like_a_registry(head):
        registry, remainder = head, rest
    else:
        registry, remainder = DEFAULT_REGISTRY, text

    tag: str | None = None
    # Only the last path segment may carry a tag — a port in the registry host has a colon too,
    # and splitting on the first one would turn `localhost:5000/app` into a tag of `5000/app`.
    repository, sep, candidate = remainder.rpartition(":")
    if sep and "/" not in candidate:
        tag = candidate
    else:
        repository = remainder

    if not repository:
        raise ValueError(f"no repository in reference: {reference!r}")
    if tag is not None and not _TAG.match(tag):
        raise ValueError(f"invalid tag: {tag!r}")

    if registry == DEFAULT_REGISTRY and "/" not in repository:
        repository = f"{OFFICIAL_NAMESPACE}/{repository}"

    # Not `tag or DEFAULT_TAG` — a digest reference has no tag and inventing `latest` for it
    # would put a name in the record that points somewhere else entirely.
    if tag is None and digest is None:
        tag = DEFAULT_TAG

    return ImageReference(registry=registry, repository=repository, tag=tag, digest=digest)
