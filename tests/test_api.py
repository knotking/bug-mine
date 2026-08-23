"""API behaviour.

The tests that matter are the negative ones: an uncovered dependency must be reported as
uncovered, an anonymous caller must never see a tenant record, and a request must not be able
to name its own tenant.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from bugmine.api import create_app
from bugmine.catalog import IncomingBug, write
from bugmine.db import tenant_session
from bugmine.models import (
    ApiKey,
    BugRecord,
    BugType,
    Job,
    JobType,
    LifecycleState,
    PrincipalKind,
    PrivacyScope,
    RecordOrigin,
    Scan,
    SubjectDomain,
    Team,
)
from bugmine.models.metering import CostBearer, UsageEvent, UsagePurpose
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select


@pytest.fixture
def client(engine: Engine) -> TestClient:
    return TestClient(create_app(engine))


def _issue_key(engine: Engine, tenant_id: uuid.UUID) -> str:
    """Mint a team-scoped key. FR-80: never tenant-wide."""
    secret = f"bmk_{uuid.uuid4().hex}"
    with tenant_session(engine, tenant_id) as s:
        team = Team(tenant_id=tenant_id, name=f"team-{uuid.uuid4().hex[:6]}")
        s.add(team)
        s.flush()
        s.add(
            ApiKey(
                tenant_id=tenant_id,
                name="ci",
                principal_kind=PrincipalKind.TEAM,
                principal_team_id=team.id,
                prefix=secret[:12],
                secret_hash=hashlib.sha256(secret.encode()).digest(),
            )
        )
    return secret


def _seed_public_bug(engine: Engine, ref: str) -> None:
    with tenant_session(engine, None) as s:
        write(
            s,
            IncomingBug(
                subject_domain=SubjectDomain.REPO_LIBRARY,
                component_ref=ref,
                ecosystem="pypi",
                bug_type=BugType.BREAKING_CHANGE,
                applicability={
                    "kind": "version_range",
                    "scheme": "semver",
                    "introduced_in": "0.3.0",
                    "fixed_in": "0.3.4",
                },
                title="Retry semantics changed for tool-calling chains",
                evidence_url="https://example.invalid/v0.3.0",
            ),
            origin=RecordOrigin.CRAWLED,
        )


class TestAuth:
    def test_check_requires_a_credential(self, client: TestClient) -> None:
        r = client.post(
            "/v1/check/dependencies", json={"dependencies": [{"ecosystem": "pypi", "name": "x"}]}
        )
        assert r.status_code == 401

    def test_a_bogus_key_is_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/v1/check/dependencies",
            json={"dependencies": [{"ecosystem": "pypi", "name": "x"}]},
            headers={"Authorization": "Bearer bmk_deadbeefdeadbeef"},
        )
        assert r.status_code == 401


class TestCheckDependencies:
    def test_returns_cited_matches(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        ref = f"lib-{uuid.uuid4().hex[:8]}"
        _seed_public_bug(engine, ref)
        key = _issue_key(engine, a)

        r = client.post(
            "/v1/check/dependencies",
            json={"dependencies": [{"ecosystem": "pypi", "name": ref, "version": "0.3.1"}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["matches"]) == 1
        m = body["matches"][0]
        assert m["version_confirmed"] is True
        # Every finding carries the record that grounds it. One without a citation is a bug.
        assert m["citation"]["bug_version_id"]
        assert m["citation"]["evidence_url"]

    def test_uncovered_dependency_is_reported_not_omitted(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """The failure an LLM-backed system makes silently.

        Returning nothing for an unknown component reads as a clean bill of health.
        """
        a, _ = two_tenants
        key = _issue_key(engine, a)
        r = client.post(
            "/v1/check/dependencies",
            json={"dependencies": [{"ecosystem": "pypi", "name": f"nope-{uuid.uuid4().hex}"}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        body = r.json()
        assert body["matches"] == []
        assert body["not_covered"][0]["reason"] == "component_not_in_catalog"

    def test_unsupported_ecosystem_is_reported(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        key = _issue_key(engine, a)
        r = client.post(
            "/v1/check/dependencies",
            json={"dependencies": [{"ecosystem": "cargo", "name": "serde", "version": "1.0"}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.json()["not_covered"][0]["reason"] == "ecosystem_unsupported"

    def test_version_outside_the_range_does_not_match(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        ref = f"lib-{uuid.uuid4().hex[:8]}"
        _seed_public_bug(engine, ref)
        key = _issue_key(engine, a)
        r = client.post(
            "/v1/check/dependencies",
            json={"dependencies": [{"ecosystem": "pypi", "name": ref, "version": "0.3.4"}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.json()["matches"] == [], "the fixed version must not match"

    def test_unknown_version_matches_unconfirmed(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        ref = f"lib-{uuid.uuid4().hex[:8]}"
        _seed_public_bug(engine, ref)
        key = _issue_key(engine, a)
        r = client.post(
            "/v1/check/dependencies",
            json={"dependencies": [{"ecosystem": "pypi", "name": ref}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        matches = r.json()["matches"]
        assert matches and matches[0]["version_confirmed"] is False


class TestPublicSearch:
    def test_anonymous_search_returns_public_records(
        self, client: TestClient, engine: Engine
    ) -> None:
        ref = f"pub-{uuid.uuid4().hex[:8]}"
        _seed_public_bug(engine, ref)
        r = client.get("/v1/public/bugs/search", params={"q": ref})
        assert r.status_code == 200
        assert any(b["component"]["ref"] == ref for b in r.json())

    def test_anonymous_search_never_returns_tenant_records(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """The leak that cannot be undone. RLS makes this true independently of the filter."""
        a, _ = two_tenants
        ref = f"secret-{uuid.uuid4().hex[:8]}"
        with tenant_session(engine, a) as s:
            write(
                s,
                IncomingBug(
                    subject_domain=SubjectDomain.REPO_LIBRARY,
                    component_ref=ref,
                    ecosystem="pypi",
                    bug_type=BugType.FUNCTIONAL,
                    applicability={"kind": "version_range"},
                    title="internal service leaks connections",
                ),
                origin=RecordOrigin.SCAN_DERIVED,
                privacy_scope=PrivacyScope.TENANT,
                tenant_id=a,
            )
        r = client.get("/v1/public/bugs/search", params={"q": ref})
        assert r.json() == []


class TestConsole:
    """The console's API surface.

    The console sends its API key on `X-BugMine-Key` rather than `Authorization`, because Cloud
    Run consumes the latter for its own IAM check on this deployment. These assert both headers
    resolve to the same principal, so the workaround does not become a second, weaker way in.
    """

    def test_console_page_is_served(self, client: TestClient) -> None:
        r = client.get("/console")
        assert r.status_code == 200
        assert "BugMine" in r.text

    def test_whoami_identifies_the_principal(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        key = _issue_key(engine, a)
        body = client.get("/v1/whoami", headers={"X-BugMine-Key": key}).json()
        assert body["tenant"]["id"] == str(a)
        assert body["principal_kind"] == "team"

    def test_both_headers_resolve_to_the_same_principal(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        key = _issue_key(engine, a)
        via_header = client.get("/v1/whoami", headers={"X-BugMine-Key": key}).json()
        via_bearer = client.get("/v1/whoami", headers={"Authorization": f"Bearer {key}"}).json()
        assert via_header == via_bearer

    def test_console_endpoints_reject_an_unknown_key(self, client: TestClient) -> None:
        for path in ("/v1/whoami", "/v1/tenant/members", "/v1/tenant/api-keys", "/v1/sources"):
            assert client.get(path, headers={"X-BugMine-Key": "bmk_nope"}).status_code == 401

    def test_api_keys_never_expose_the_secret(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        key = _issue_key(engine, a)
        body = client.get("/v1/tenant/api-keys", headers={"X-BugMine-Key": key}).json()
        assert body
        for row in body:
            assert key not in str(row), "a key listing leaked a usable secret"
            assert "secret" not in row

    def test_a_tenant_sees_only_its_own_sources(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, b = two_tenants
        key_a, key_b = _issue_key(engine, a), _issue_key(engine, b)
        url = f"https://example.test/{uuid.uuid4().hex[:8]}"
        client.post(
            "/v1/sources",
            headers={"X-BugMine-Key": key_a},
            json={"url": url, "component_ref": "x", "ecosystem": "pypi"},
        )
        seen_by_b = client.get("/v1/sources", headers={"X-BugMine-Key": key_b}).json()
        assert all(s["url"] != url for s in seen_by_b), "tenant B saw tenant A's source"

    def test_a_console_added_source_is_tenant_scoped(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, _ = two_tenants
        key = _issue_key(engine, a)
        r = client.post(
            "/v1/sources",
            headers={"X-BugMine-Key": key},
            json={"url": f"https://example.test/{uuid.uuid4().hex[:8]}"},
        )
        assert r.json()["scope"] == "tenant", "a console-added source must never be public"


class TestSweepTrigger:
    """The scheduled sweep endpoint.

    Gated by the operator token rather than a tenant key: a sweep enqueues crawls, and a crawl
    fetches an arbitrary URL with our egress. No customer credential should be able to start one.
    """

    def test_it_requires_the_operator_token(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUGMINE_OPERATOR_TOKEN", "secret")
        assert client.post("/v1/admin/ingest/sweep").status_code == 401
        assert (
            client.post(
                "/v1/admin/ingest/sweep", headers={"X-BugMine-Operator": "wrong"}
            ).status_code
            == 401
        )

    def test_it_reports_what_it_enqueued(
        self, client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUGMINE_OPERATOR_TOKEN", "secret")
        sent: list[dict] = []
        monkeypatch.setattr("bugmine.tasks.enqueue", lambda target, payload: sent.append(payload))
        monkeypatch.setattr("bugmine.tasks.crawl_target", lambda: None)

        from bugmine.db import tenant_session
        from bugmine.models import SubjectDomain
        from bugmine.sweep import add_source

        url = f"https://example.test/{uuid.uuid4().hex[:8]}"
        with tenant_session(engine, None) as s:
            add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)

        body = client.post(
            "/v1/admin/ingest/sweep", headers={"X-BugMine-Operator": "secret"}
        ).json()
        assert body["enqueued"] >= 1
        assert any(p["url"] == url for p in sent)

    def test_sweeping_twice_enqueues_once(
        self, client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scheduler that double-fires must not double-crawl."""
        monkeypatch.setenv("BUGMINE_OPERATOR_TOKEN", "secret")
        sent: list[dict] = []
        monkeypatch.setattr("bugmine.tasks.enqueue", lambda target, payload: sent.append(payload))
        monkeypatch.setattr("bugmine.tasks.crawl_target", lambda: None)

        from bugmine.db import tenant_session
        from bugmine.models import SubjectDomain
        from bugmine.sweep import add_source

        url = f"https://example.test/{uuid.uuid4().hex[:8]}"
        with tenant_session(engine, None) as s:
            add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)

        client.post("/v1/admin/ingest/sweep", headers={"X-BugMine-Operator": "secret"})
        client.post("/v1/admin/ingest/sweep", headers={"X-BugMine-Operator": "secret"})
        assert sum(1 for p in sent if p["url"] == url) == 1


class TestRetractedRecordsAreNotSearchable:
    """A retracted record is one we have said should not have been published.

    Leaving it searchable republishes it, which is the whole failure retraction exists to
    undo — so both search routes must exclude it, not only the matcher used by scans.
    """

    def _retract_all(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            for record in s.execute(select(BugRecord)).scalars():
                record.lifecycle_state = LifecycleState.RETRACTED

    def test_public_search_excludes_retracted(self, client: TestClient, engine: Engine) -> None:
        _seed_public_bug(engine, "langchain")
        assert client.get("/v1/public/bugs/search?q=langchain").json() != []
        self._retract_all(engine)
        assert client.get("/v1/public/bugs/search?q=langchain").json() == []

    def test_console_search_excludes_retracted(
        self,
        client: TestClient,
        engine: Engine,
        two_tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        tenant, _ = two_tenants
        key = _issue_key(engine, tenant)
        # A distinct component from the public-search test: records are content-hash deduped,
        # so re-seeding an identical bug would match the already-retracted row rather than
        # create a fresh one, and the test would pass for the wrong reason.
        _seed_public_bug(engine, "langgraph")
        headers = {"X-BugMine-Key": key}
        assert client.get("/v1/catalog/search?q=langgraph", headers=headers).json() != []
        self._retract_all(engine)
        assert client.get("/v1/catalog/search?q=langgraph", headers=headers).json() == []

    def test_summary_does_not_count_retracted(
        self,
        client: TestClient,
        engine: Engine,
        two_tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """The console shows the summary and the search results on one screen. If the counts
        include records search omits, the page contradicts itself and overstates coverage."""
        tenant, _ = two_tenants
        key = _issue_key(engine, tenant)
        _seed_public_bug(engine, "llamaindex")
        headers = {"X-BugMine-Key": key}
        before = client.get("/v1/catalog/summary", headers=headers).json()
        assert before["records"] == 1
        assert before["components"] == 1

        self._retract_all(engine)
        after = client.get("/v1/catalog/summary", headers=headers).json()
        assert after["records"] == 0
        assert after["by_type"] == {}
        # The component row survives retraction; coverage must not.
        assert after["components"] == 0


class TestConsoleIsNotCached:
    def test_the_console_is_served_no_store(self, client: TestClient) -> None:
        """One unversioned document holding all the HTML, CSS and JS, with no build step and no
        content-hashed name. Without this the browser keeps the copy it already has, so a
        deployed fix never arrives — and a still-broken page looks identical to a fix that did
        not work, which cost three deploys' worth of wrong diagnoses.
        """
        r = client.get("/console")
        assert r.status_code == 200
        assert "no-store" in r.headers.get("cache-control", "")


class TestUsageAccounting:
    """Token accounting at team and user level.

    The ledger has recorded this since the metering work and nothing exposed it, so the
    accounting that was asked for existed and was invisible.
    """

    def _spend(
        self,
        engine: Engine,
        tenant: uuid.UUID,
        *,
        team_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        bearer: CostBearer = CostBearer.TENANT,
        cost: int = 1000,
        succeeded: bool = True,
    ) -> None:
        with tenant_session(engine, None if bearer is CostBearer.SYSTEM else tenant) as s:
            s.add(
                UsageEvent(
                    cost_bearer=bearer,
                    tenant_id=None if bearer is CostBearer.SYSTEM else tenant,
                    team_id=team_id,
                    user_id=user_id,
                    purpose=UsagePurpose.EXTRACT,
                    model_id="gemini-3.7-flash",
                    input_tokens=100,
                    output_tokens=50,
                    rate_card_version="v1",
                    cost_micros=cost,
                    succeeded=succeeded,
                )
            )

    def test_usage_totals_the_tenants_own_spend(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        tenant, _ = two_tenants
        key = _issue_key(engine, tenant)
        self._spend(engine, tenant, cost=2500)
        body = client.get("/v1/usage", headers={"X-BugMine-Key": key}).json()
        assert body["cost_micros"] == 2500
        assert body["input_tokens"] == 100

    def test_system_spend_is_excluded(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """Crawling the world is what makes the catalog exist. It does not vary with a tenant's
        usage, and showing it here would read as a bill they owe."""
        tenant, _ = two_tenants
        key = _issue_key(engine, tenant)
        before = client.get("/v1/usage", headers={"X-BugMine-Key": key}).json()["cost_micros"]
        self._spend(engine, tenant, bearer=CostBearer.SYSTEM, cost=999_999)
        after = client.get("/v1/usage", headers={"X-BugMine-Key": key}).json()["cost_micros"]
        assert after == before

    def test_failed_calls_are_counted_separately(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """A retry loop that never succeeds still spends. A total that hid it would look idle."""
        tenant, _ = two_tenants
        key = _issue_key(engine, tenant)
        self._spend(engine, tenant, succeeded=False, cost=700)
        body = client.get("/v1/usage", headers={"X-BugMine-Key": key}).json()
        assert sum(line["failed_calls"] for line in body["by_purpose"]) >= 1

    def test_a_tenant_never_sees_another_tenants_usage(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, b = two_tenants
        key_a = _issue_key(engine, a)
        self._spend(engine, b, cost=444_444)
        body = client.get("/v1/usage", headers={"X-BugMine-Key": key_a}).json()
        assert body["cost_micros"] != 444_444


class TestKeyMinting:
    def test_a_key_can_be_minted_and_used(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        tenant, _ = two_tenants
        key = _issue_key(engine, tenant)
        headers = {"X-BugMine-Key": key}
        teams = client.get("/v1/tenant/api-keys", headers=headers).json()
        assert teams  # the issuing key is listed

        with tenant_session(engine, tenant) as s:
            team_id = str(s.execute(select(Team)).scalars().first().id)

        r = client.post(
            "/v1/tenant/api-keys",
            headers=headers,
            json={"name": "ci", "team_id": team_id},
        )
        assert r.status_code == 201
        minted = r.json()["secret"]
        assert minted.startswith("bmk_")
        # The point of minting: the new key authenticates.
        assert client.get("/v1/whoami", headers={"X-BugMine-Key": minted}).status_code == 200

    def test_a_key_must_name_exactly_one_principal(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """FR-80. Spend is attributed per team and per user; a tenant-wide key could populate
        neither, so per-team reporting would be structurally impossible."""
        tenant, _ = two_tenants
        headers = {"X-BugMine-Key": _issue_key(engine, tenant)}
        r = client.post("/v1/tenant/api-keys", headers=headers, json={"name": "bad"})
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "one_principal_required"

    def test_a_revoked_key_stops_working(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        tenant, _ = two_tenants
        headers = {"X-BugMine-Key": _issue_key(engine, tenant)}
        with tenant_session(engine, tenant) as s:
            team_id = str(s.execute(select(Team)).scalars().first().id)
        created = client.post(
            "/v1/tenant/api-keys", headers=headers, json={"name": "temp", "team_id": team_id}
        ).json()
        minted = {"X-BugMine-Key": created["secret"]}
        assert client.get("/v1/whoami", headers=minted).status_code == 200

        revoked = client.delete(f"/v1/tenant/api-keys/{created['id']}", headers=headers)
        assert revoked.status_code == 204
        assert client.get("/v1/whoami", headers=minted).status_code == 401


class TestWritesActuallyPersist:
    """Round-trip, not just isolation.

    The isolation test asserted only that tenant B could not see tenant A's source — which
    passes trivially when the source was never written. The console session was read-only, so
    every write through it returned success and was discarded: POST /v1/sources answered 201
    with a populated body and left the table empty.
    """

    def test_a_source_survives_the_request_that_created_it(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        tenant, _ = two_tenants
        headers = {"X-BugMine-Key": _issue_key(engine, tenant)}
        url = f"https://example.test/{uuid.uuid4().hex[:8]}"
        assert client.post(
            "/v1/sources", headers=headers,
            json={"url": url, "component_ref": "x", "ecosystem": "pypi"},
        ).status_code == 201
        seen = client.get("/v1/sources", headers=headers).json()
        assert any(s["url"] == url for s in seen), "the source did not persist"

    def test_a_read_route_cannot_commit(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """Read handlers keep the non-committing session, so a stray write stays discarded."""
        import inspect

        from bugmine.api import app as app_module

        source = inspect.getsource(app_module)
        for route in ('@console.get("/tenant/members"', '@console.get("/usage"'):
            block = source[source.index(route) : source.index("\n\n\n", source.index(route))]
            assert "tenant_db_write" not in block, f"{route} uses a committing session"


class TestScanSubmission:
    def test_a_private_target_is_refused_before_it_reaches_a_queue(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """Rejecting at submission rather than in the worker means a denied target never sits
        in a queue, where the dispatcher would retry it long after the caller was told no."""
        tenant, _ = two_tenants
        headers = {"X-BugMine-Key": _issue_key(engine, tenant)}
        r = client.post(
            "/v1/scans", headers=headers, json={"repo_url": "http://169.254.169.254/meta"}
        )
        assert r.status_code == 422

    def test_findings_are_scoped_to_the_tenant(
        self, client: TestClient, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """Another tenant's scan is absent, not forbidden — a 403 confirms it exists."""
        a, b = two_tenants
        with tenant_session(engine, b) as s:
            job = Job(tenant_id=b, job_type=JobType.SCAN_FETCH, payload={})
            s.add(job)
            s.flush()
            scan = Scan(job_id=job.id, tenant_id=b, repo_ref="https://example.invalid/b.git")
            s.add(scan)
            s.flush()
            other_scan = str(scan.id)

        headers = {"X-BugMine-Key": _issue_key(engine, a)}
        assert client.get(f"/v1/scans/{other_scan}/findings", headers=headers).json() == []
        assert all(s["id"] != other_scan for s in client.get("/v1/scans", headers=headers).json())
