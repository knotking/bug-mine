"""Fakes for the integration harness.

Only two things are faked: object storage and the model. Everything else — Postgres, RLS, the
catalog, the API, the workers — is the real code path, because the defects worth catching live
there. Eight were found in the deployed environment during the first E2E run; every one would
have surfaced here.

Notably *not* faked: the database. RLS is the property most of those defects violated, and it
has no in-memory equivalent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self._bucket = bucket
        self.name = name
        self.metadata: dict[str, str] | None = None

    def exists(self) -> bool:
        return self.name in self._bucket.objects

    def upload_from_string(self, data: str | bytes, content_type: str = "") -> None:
        self._bucket.objects[self.name] = data if isinstance(data, bytes) else data.encode()

    def download_as_text(self) -> str:
        return self._bucket.objects[self.name].decode()

    def download_as_bytes(self) -> bytes:
        """Snapshots are gzipped tar, so the bytes path is not optional — decoding one as text
        corrupts it in a way that only surfaces as an unhelpful tarfile error."""
        return self._bucket.objects[self.name]


@dataclass
class FakeBucket:
    name: str
    objects: dict[str, bytes] = field(default_factory=dict)

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


class FakeStorageClient:
    """Stands in for google.cloud.storage.Client."""

    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket(name))


@dataclass
class _Response:
    text: str


class FakeModels:
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if isinstance(self._payload, Exception):
            raise self._payload
        return _Response(text=json.dumps(self._payload))


class FakeGenAIClient:
    """Stands in for google.genai.Client.

    Accepts an exception to raise, so the harness can reproduce the model being unavailable —
    which is the deployed reality here, and the case that must not silently corrupt anything.
    """

    def __init__(self, payload: dict[str, Any] | Exception | None = None) -> None:
        self.models = FakeModels(payload if payload is not None else {"bugs": []})
