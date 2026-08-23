"""Test fixtures.

Tests run against a real Postgres rather than SQLite. Row-level security, native enums, and
JSONB have no SQLite equivalent, and RLS is precisely what the most important tests assert —
so an in-memory substitute would pass while the property under test was absent.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from bugmine.db import make_engine
from bugmine.models import Tenant
from sqlalchemy import Engine, text
from starlette.testclient import TestClient

ADMIN_URL = os.environ.get(
    "BUGMINE_TEST_ADMIN_URL",
    "postgresql+psycopg://postgres:dev@localhost:55432/bugmine_test",
)

# Tests connect as the unprivileged application role, never as the owner.
#
# This is not fastidiousness. Superusers and table owners bypass row-level security, so a test
# suite that connects as `postgres` asserts tenant isolation against a database that is not
# enforcing any — every isolation test passes while the property is absent. Production has the
# same requirement: the Cloud SQL application user must not be an owner.
APP_ROLE = "bugmine_app"
APP_PASSWORD = "test-only"
APP_URL = ADMIN_URL.replace("postgres:dev@", f"{APP_ROLE}:{APP_PASSWORD}@")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    server_url = ADMIN_URL.rsplit("/", 1)[0] + "/postgres"
    db_name = ADMIN_URL.rsplit("/", 1)[1]

    admin = make_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        conn.execute(
            text(
                f"DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
                f"NOSUPERUSER NOBYPASSRLS; END IF; END $$;"
            )
        )
    admin.dispose()

    # Migrations run as the owner; the application role only gets DML.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        env={**os.environ, "BUGMINE_DATABASE_URL": ADMIN_URL},
        check=True,
        capture_output=True,
    )

    owner = make_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with owner.connect() as conn:
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        conn.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
            )
        )
        conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
    owner.dispose()

    eng = make_engine(APP_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def two_tenants(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Two tenants, created with RLS bypassed via a session that owns neither."""
    from bugmine.db import tenant_session

    a, b = uuid.uuid4(), uuid.uuid4()
    with tenant_session(engine, None) as s:
        s.add(Tenant(id=a, name="Tenant A", slug=f"a-{a.hex[:8]}"))
        s.add(Tenant(id=b, name="Tenant B", slug=f"b-{b.hex[:8]}"))
    return a, b


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    """Connects as the table owner, where RLS does not apply.

    Used only to test constraints that RLS would otherwise mask by rejecting the write first.
    """
    eng = make_engine(ADMIN_URL)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _resolvable_test_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make .test hostnames resolve to a public address for the duration of a test.

    The SSRF guard refuses unresolvable hosts, which is correct — but it means fixtures using
    example.test would be rejected for the wrong reason and mask what the test is checking.
    Only the resolver is stubbed; every other rule still applies, so a test that reaches for
    localhost or a private range is still refused.
    """
    from bugmine import urlguard

    real = urlguard._resolve

    def fake(host: str) -> list[str]:
        if host.endswith(".test"):
            return ["93.184.216.34"]
        return real(host)

    monkeypatch.setattr(urlguard, "_resolve", fake)


@pytest.fixture
def worker_client(engine: Engine) -> Iterator[TestClient]:
    """The worker app with its cloud dependencies faked.

    Storage is a fake rather than a mock so a snapshot really is written and read back — the
    tar round-trip is where path traversal and encoding problems live, and a mock would assert
    the call was made while proving nothing about the bytes.

    The model client is never constructed: the analysis path is static narrowing only, and
    building a real Vertex client here would make the tests need credentials to run.
    """
    from unittest.mock import patch

    from fakes import FakeStorageClient

    with patch("bugmine.worker.app.storage.Client", FakeStorageClient), patch(
        "bugmine.worker.app.genai.Client", lambda **_: None
    ):
        from bugmine.worker.app import create_worker_app

        app = create_worker_app(engine)
        app.state.snapshot_bucket = "snap"
        app.state.artifact_bucket = "artifacts"
        yield TestClient(app)
