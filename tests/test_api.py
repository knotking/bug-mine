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
    BugType,
    PrincipalKind,
    PrivacyScope,
    RecordOrigin,
    SubjectDomain,
    Team,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine


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
