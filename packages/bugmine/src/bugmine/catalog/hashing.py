"""Content hashing — the gate that bounds catalog growth.

A crawl re-fetches the same changelog every day. Without this, FR-5's versioning mints a new
row on every fetch and catalog size becomes a function of crawl *frequency* rather than of how
often the software actually changed. The MVP cost estimate depends on it directly: extraction
runs on the ~5% of artifacts that changed rather than on all of them.

The hash covers only fields that carry meaning. Crawl timestamps and artifact URIs deliberately
do not participate — including them would make every fetch look like a change, which is the
failure this exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def content_hash(
    *,
    title: str,
    description: str | None,
    evidence_url: str | None,
    applicability: dict[str, Any],
) -> bytes:
    payload = {
        "title": title.strip(),
        "description": (description or "").strip(),
        "evidence_url": (evidence_url or "").strip(),
        # sort_keys so a dict rebuilt in a different order hashes identically
        "applicability": applicability,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).digest()
