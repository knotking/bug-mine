"""End-to-end through the real code, in one process.

This is the harness that should have existed before the first deployment. It runs the actual
crawl worker, extraction, catalog write path, and API against a real Postgres with RLS
enforced, faking only object storage and the model.

Each test below corresponds to a defect that was found in production during the first E2E run,
at roughly five minutes per discovery.
"""

from __future__ import annotations

import json
import uuid
from typing import ClassVar

import httpx
import pytest
from bugmine.api import create_app
from bugmine.db import tenant_session
from bugmine.models import JobType, SubjectDomain
from bugmine.models.jobs import Job
from bugmine.worker import crawl as crawl_mod
from bugmine.worker import extract as extract_mod
from bugmine.worker import structured
from fakes import FakeGenAIClient, FakeStorageClient
from fastapi.testclient import TestClient
from sqlalchemy import Engine

BUCKET = "test-artifacts"


def _releases(n: int, *, body: str = "## Breaking Changes\n- removed the old API") -> str:
    return json.dumps(
        [
            {
                "tag_name": f"v2.{i}.0",
                "name": f"v2.{i}.0",
                "body": body,
                "html_url": f"https://gh.test/r/{i}",
            }
            for i in range(n)
        ]
    )


@pytest.fixture
def storage() -> FakeStorageClient:
    return FakeStorageClient()


class TestCrawlToArtifact:
    def test_fetch_stores_under_a_content_hash(
        self, storage: FakeStorageClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = _releases(3)

        class _Resp:
            content = body.encode()
            headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

            def raise_for_status(self) -> None: ...

        class _Client:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *a: object) -> None: ...

            def get(self, *a: object, **k: object) -> _Resp:
                return _Resp()

        monkeypatch.setattr(httpx, "Client", lambda **k: _Client())

        first = crawl_mod.fetch_and_store("https://x.test", bucket_name=BUCKET, client=storage)
        assert first.changed, "a first fetch must be treated as new"

        second = crawl_mod.fetch_and_store("https://x.test", bucket_name=BUCKET, client=storage)
        assert second.deduplicated, "unchanged content must not be stored again"
        assert second.uri == first.uri
        assert len(storage.bucket(BUCKET).objects) == 1


class TestArtifactToCatalog:
    def test_a_large_feed_reaches_the_catalog(
        self, engine: Engine, storage: FakeStorageClient
    ) -> None:
        """Regression for the truncation defect.

        A 1.2 MB feed was cut at read time, became invalid JSON, and fell through to a model
        that was unavailable — so a document the parser handles perfectly failed twice.
        """
        feed = _releases(400, body="## Breaking Changes\n" + ("filler " * 400))
        assert len(feed) > 500_000

        blob = storage.bucket(BUCKET).blob("aa/hash")
        blob.upload_from_string(feed)

        document = extract_mod.read_artifact(f"gs://{BUCKET}/aa/hash", client=storage)
        assert len(document) == len(feed), "read must not truncate"
        assert structured.looks_like_github_releases(document)

        parsed = structured.extract_github_releases(document, component_ref="big", ecosystem="pypi")
        assert len(parsed.bugs) == 400

    def test_the_model_being_unavailable_writes_nothing(
        self, engine: Engine, storage: FakeStorageClient
    ) -> None:
        """The deployed reality. Failure must be clean, not partial."""
        blob = storage.bucket(BUCKET).blob("bb/hash")
        blob.upload_from_string("# Changelog\n\nSome prose that needs a model.")

        with pytest.raises(RuntimeError, match="unavailable"):
            extract_mod.extract(
                extract_mod.read_artifact(f"gs://{BUCKET}/bb/hash", client=storage),
                genai_client=FakeGenAIClient(RuntimeError("model unavailable")),
                model="absent",
                subject_domain=SubjectDomain.REPO_LIBRARY,
                ecosystem="pypi",
            )


class TestSystemJobsThroughTheRealPath:
    def test_a_crawl_job_row_can_be_written(self, engine: Engine) -> None:
        """Regression for the RLS defect that broke ingestion entirely.

        A global crawl has no tenant. The policy refused exactly those rows, so the worker
        could not record its own work and every task failed after five dispatch attempts.
        """
        with tenant_session(engine, None) as s:
            s.add(Job(job_type=JobType.CRAWL, tenant_id=None, payload={"url": "https://x.test"}))


class TestCatalogToApi:
    def test_records_are_retrievable_and_cited(self, engine: Engine) -> None:
        from bugmine.catalog import write
        from bugmine.models import RecordOrigin

        ref = f"lib-{uuid.uuid4().hex[:8]}"
        parsed = structured.extract_github_releases(
            _releases(2), component_ref=ref, ecosystem="pypi", artifact_uri="gs://b/o"
        )
        with tenant_session(engine, None) as s:
            for bug in parsed.bugs:
                write(s, bug, origin=RecordOrigin.CRAWLED)

        client = TestClient(create_app(engine))
        found = client.get("/v1/public/bugs/search", params={"q": ref}).json()
        assert found, "records written by the real path are not retrievable"
        assert all(b["evidence_url"] for b in found), "a finding without evidence is not grounded"

    def test_dedup_survives_a_second_extraction(self, engine: Engine) -> None:
        """Re-extracting the same artifact must not fork the catalog."""
        from bugmine.catalog import write
        from bugmine.models import RecordOrigin

        ref = f"lib-{uuid.uuid4().hex[:8]}"
        parsed = structured.extract_github_releases(
            _releases(2), component_ref=ref, ecosystem="pypi"
        )

        for _ in range(2):
            with tenant_session(engine, None) as s:
                results = [write(s, b, origin=RecordOrigin.CRAWLED) for b in parsed.bugs]

        assert all(r.deduplicated for r in results), "a repeat extraction created versions"


class TestGitHubTokenScoping:
    """The crawler fetches URLs a tenant chose, so the token must reach GitHub and nowhere else.

    It carries no scopes and reads only public data, but a credential sent to a stranger is
    disclosed regardless of what it can do.
    """

    def test_the_token_is_sent_to_github(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from bugmine.worker.crawl import _headers_for

        monkeypatch.setenv("BUGMINE_GITHUB_TOKEN", "secret-token")
        headers = _headers_for("https://api.github.com/repos/x/y/releases")
        assert headers["Authorization"] == "Bearer secret-token"

    def test_the_token_is_withheld_from_everyone_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bugmine.worker.crawl import _headers_for

        monkeypatch.setenv("BUGMINE_GITHUB_TOKEN", "secret-token")
        for url in (
            "https://example.invalid/releases",
            # Ends with the string and is not GitHub: a suffix match would leak here.
            "https://github.com.evil.test/repos",
            "http://169.254.169.254/latest/meta-data/",
        ):
            assert "Authorization" not in _headers_for(url), url

    def test_no_token_configured_sends_no_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from bugmine.worker.crawl import _headers_for

        monkeypatch.delenv("BUGMINE_GITHUB_TOKEN", raising=False)
        assert "Authorization" not in _headers_for("https://api.github.com/repos/x/y/releases")
