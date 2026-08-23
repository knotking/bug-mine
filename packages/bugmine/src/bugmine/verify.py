"""Deployment verification — the checks that must hold against the real database.

Runs as a Cloud Run job because Cloud SQL has no public IP. Every check here is one that
**fails open**: if the property is absent, normal operation looks identical and no error is
raised. That is why they are asserted explicitly rather than assumed from the schema.

Exit code is non-zero if any check fails, so this can gate a deployment.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from bugmine.db import make_engine, tenant_session

TENANT_TABLES = ["team", "membership", "invite", "job", "scan"]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _check_role_cannot_bypass_rls(engine) -> list[Check]:  # type: ignore[no-untyped-def]
    """The check that makes every other isolation check meaningful.

    Cloud SQL grants cloudsqlsuperuser to the application user. A role that is a superuser or
    holds BYPASSRLS ignores row-level security entirely, so isolation tests pass against a
    database enforcing nothing — which has already happened once in this codebase's local
    suite.
    """
    with tenant_session(engine, None, commit=False) as s:
        row = s.execute(
            text(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    user, is_super, bypasses = row
    return [
        Check("role is not a superuser", not is_super, f"{user}: rolsuper={is_super}"),
        Check("role does not bypass RLS", not bypasses, f"{user}: rolbypassrls={bypasses}"),
    ]


def _check_rls_enabled(engine) -> list[Check]:  # type: ignore[no-untyped-def]
    with tenant_session(engine, None, commit=False) as s:
        rows = s.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r'"
            )
        ).all()
    state = {r[0]: (r[1], r[2]) for r in rows}
    checks = []
    for table in [*TENANT_TABLES, "bug_record", "bug_version", "api_key"]:
        enabled, forced = state.get(table, (False, False))
        checks.append(
            Check(
                f"RLS enabled and forced on {table}",
                enabled and forced,
                f"enabled={enabled} forced={forced}",
            )
        )
    return checks


def _check_cross_tenant_isolation(engine) -> list[Check]:  # type: ignore[no-untyped-def]
    """Write a row as one tenant, then try to read it as another."""
    from bugmine.models import JobType, Tenant
    from bugmine.models.jobs import Job

    a, b = uuid.uuid4(), uuid.uuid4()
    marker = uuid.uuid4().hex

    with tenant_session(engine, None) as s:
        s.add(Tenant(id=a, name="verify-a", slug=f"verify-a-{a.hex[:8]}"))
        s.add(Tenant(id=b, name="verify-b", slug=f"verify-b-{b.hex[:8]}"))

    with tenant_session(engine, a) as s:
        s.add(Job(job_type=JobType.NOOP, tenant_id=a, payload={"marker": marker}))

    with tenant_session(engine, b, commit=False) as s:
        seen_by_b = s.execute(
            text("SELECT count(*) FROM job WHERE payload->>'marker' = :m"), {"m": marker}
        ).scalar_one()

    with tenant_session(engine, None, commit=False) as s:
        seen_anon = s.execute(
            text("SELECT count(*) FROM job WHERE payload->>'marker' = :m"), {"m": marker}
        ).scalar_one()

    with tenant_session(engine, a, commit=False) as s:
        seen_by_a = s.execute(
            text("SELECT count(*) FROM job WHERE payload->>'marker' = :m"), {"m": marker}
        ).scalar_one()

    # Clean up *every* verification fixture, not only this run's.
    #
    # Verification writes to the real database. An earlier version of this job had no cleanup
    # at all, so residue accumulated — and a production tenant list filling with `verify-*`
    # entries is indistinguishable from a data-quality problem when somebody finds it later.
    # Sweeping the whole prefix makes the job self-healing rather than merely well-behaved
    # from now on.
    with tenant_session(engine, None) as s:
        s.execute(text("DELETE FROM job WHERE payload->>'marker' = :m"), {"m": marker})
        s.execute(text("DELETE FROM tenant WHERE slug LIKE 'verify-%'"))

    with tenant_session(engine, None, commit=False) as s:
        residue = s.execute(
            text("SELECT count(*) FROM tenant WHERE slug LIKE 'verify-%'")
        ).scalar_one()

    return [
        Check("tenant B cannot see tenant A's job", seen_by_b == 0, f"rows={seen_by_b}"),
        Check("anonymous session sees no tenant jobs", seen_anon == 0, f"rows={seen_anon}"),
        Check("tenant A can see its own job", seen_by_a == 1, f"rows={seen_by_a}"),
        Check("verification left no fixtures behind", residue == 0, f"verify-* tenants={residue}"),
    ]


def _check_schema_present(engine) -> list[Check]:  # type: ignore[no-untyped-def]
    with tenant_session(engine, None, commit=False) as s:
        n = s.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        ).scalar_one()
    return [Check("schema is migrated", n >= 15, f"{n} tables")]


def main() -> int:
    engine = make_engine()
    checks: list[Check] = []
    checks += _check_schema_present(engine)
    checks += _check_role_cannot_bypass_rls(engine)
    checks += _check_rls_enabled(engine)
    checks += _check_cross_tenant_isolation(engine)

    failed = 0
    for c in checks:
        mark = "PASS" if c.passed else "FAIL"
        if not c.passed:
            failed += 1
        print(f"[{mark}] {c.name} — {c.detail}", flush=True)

    print(f"\n{len(checks) - failed}/{len(checks)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
