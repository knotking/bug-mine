"""The crawl worker — fetch a source, store it verbatim, stop if nothing changed.

Fetching and interpreting are separate stages with durable storage between them. When the
extraction prompt or model improves, the whole corpus can be re-extracted from GCS without
re-crawling the internet; fusing the two would make every model improvement a full re-crawl.

This worker has network egress and **no model**. It never interprets what it fetched.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx
from google.cloud import storage

from bugmine import urlguard


@dataclass(frozen=True)
class CrawlResult:
    uri: str
    content_hash: str
    bytes_fetched: int
    deduplicated: bool

    @property
    def changed(self) -> bool:
        return not self.deduplicated


def fetch_and_store(
    url: str, *, bucket_name: str, client: storage.Client, timeout: float = 30.0
) -> CrawlResult:
    """Fetch a URL and store the body under its content hash.

    Keying on the hash means an unchanged source writes nothing and downstream extraction never
    runs. Without it, versioning tracks how often we look rather than how often the software
    changed — and extraction cost follows.
    """
    # Re-validated here, not only at write time. A URL that was permitted when the source was
    # created can resolve somewhere else by the time it is fetched, which is the whole point of
    # DNS rebinding. Terraform's egress deny is the third layer beneath both.
    if rejection := urlguard.check(url):
        raise ValueError(f"{rejection.code}: {rejection.message}")

    with httpx.Client(timeout=timeout, follow_redirects=False) as http:
        response = http.get(url, headers={"User-Agent": "BugMine/0.1 (+https://bugmine.dev)"})
        response.raise_for_status()
        body = response.content

    digest = hashlib.sha256(body).hexdigest()
    blob_name = f"{digest[:2]}/{digest}"
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if blob.exists():
        return CrawlResult(
            uri=f"gs://{bucket_name}/{blob_name}",
            content_hash=digest,
            bytes_fetched=len(body),
            deduplicated=True,
        )

    blob.metadata = {"source_url": url}
    blob.upload_from_string(body, content_type=response.headers.get("content-type", "text/plain"))
    return CrawlResult(
        uri=f"gs://{bucket_name}/{blob_name}",
        content_hash=digest,
        bytes_fetched=len(body),
        deduplicated=False,
    )
