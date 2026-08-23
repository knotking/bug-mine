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
    LifecycleState,
    PrincipalKind,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
    Team,
)
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
