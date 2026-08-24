"""The crawl worker — fetch a source, store it verbatim, stop if nothing changed.

Fetching and interpreting are separate stages with durable storage between them. When the
extraction prompt or model improves, the whole corpus can be re-extracted from GCS without
re-crawling the internet; fusing the two would make every model improvement a full re-crawl.

This worker has network egress and **no model**. It never interprets what it fetched.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from urllib.parse import urlparse

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
        response, final_url = _get_following_redirects(http, url, timeout=timeout)
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

    blob.metadata = {"source_url": url, "fetched_from": final_url}
    blob.upload_from_string(body, content_type=response.headers.get("content-type", "text/plain"))
    return CrawlResult(
        uri=f"gs://{bucket_name}/{blob_name}",
        content_hash=digest,
        bytes_fetched=len(body),
        deduplicated=False,
    )


MAX_REDIRECTS = 5
"""Enough for the http→https→canonical-host chains real documentation sites use, few enough
that a redirect loop fails fast rather than tying up a worker."""


def _get_following_redirects(
    http: httpx.Client, url: str, *, timeout: float
) -> tuple[httpx.Response, str]:
    """Follow redirects one hop at a time, re-validating every hop.

    httpx's own `follow_redirects=True` is unusable here: it would fetch each `Location`
    without asking `urlguard`, so an allowlisted URL that redirects to 169.254.169.254 or to
    an internal host would be fetched anyway — the exact hole the guard exists to close.

    Not following them at all was the other extreme, and it quietly cost coverage. Vendor
    documentation moves; `cloud.google.com/vertex-ai/...` now answers 301 to `docs.cloud
    .google.com`. That source failed on every sweep, escalated its backoff, and was on its way
    out of the catalog over a URL that had simply been renamed and was naming its own successor
    in the response.

    So: follow, but through the guard, and re-derive the headers each hop — the GitHub token
    must not follow a redirect off GitHub.
    """
    seen = {url}
    current = url
    for _ in range(MAX_REDIRECTS):
        response = http.get(current, headers=_headers_for(current))
        if not response.is_redirect:
            response.raise_for_status()
            return response, current

        location = response.headers.get("location")
        if not location:
            raise ValueError(f"redirect from {current} with no Location header")
        target = str(response.url.join(location))

        if rejection := urlguard.check(target):
            raise ValueError(f"redirect to {target} rejected: {rejection.code}")
        if target in seen:
            raise ValueError(f"redirect loop at {target}")
        seen.add(target)
        current = target

    raise ValueError(f"more than {MAX_REDIRECTS} redirects starting at {url}")


GITHUB_HOSTS = frozenset({"api.github.com", "github.com", "raw.githubusercontent.com"})
"""Exact hosts, not a suffix match. `github.com.evil.test` ends with the string and is not
GitHub, and this worker fetches URLs a tenant chose."""


def _headers_for(url: str) -> dict[str, str]:
    """Base headers, plus the GitHub token only when the host really is GitHub.

    Attaching it unconditionally would hand the credential to every host the crawler is pointed
    at — and it is pointed at arbitrary URLs by design. The token carries no scopes and reads
    only public data, but a credential sent to a stranger is a credential disclosed regardless
    of what it can do.
    """
    headers = {"User-Agent": "BugMine/0.1 (+https://bugmine.dev)"}
    token = os.environ.get("BUGMINE_GITHUB_TOKEN", "").strip()
    if token and urlparse(url).hostname in GITHUB_HOSTS:
        headers["Authorization"] = f"Bearer {token}"
        # Pins the response shape; GitHub's default has changed before.
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers
