"""Structural check: every tenant-scoped table has a policy that handles its exception.

Five policies of the same shape have needed correcting during this build, each found only when
something failed at runtime. The recurring mistake is writing the tenant predicate first and
discovering the exception afterwards:

- `api_key` establishes which tenant a request belongs to, so a policy keyed on the tenant
  already being known hides the row needed to learn it.
- `invite` is redeemed before the caller knows the tenant.
- `job` may legitimately have no tenant at all — a global crawl belongs to the system.

This test encodes that inventory. A new tenant-scoped table fails it until someone states
which case it is, which is the point: the failure is a prompt to decide rather than a bug
discovered in production.
"""

from __future__ import annotations

from bugmine.db import tenant_session
from sqlalchemy import Engine, text

# Every table carrying tenant data, and the exception its policy must handle.
EXPECTED = {
    "team": "own_tenant_only",
    "membership": "own_tenant_only",
    "scan": "own_tenant_only",
    "api_key": "readable_without_tenant_context",  # establishes the tenant
    "invite": "readable_without_tenant_context",  # redeemed before the tenant is known
    "job": "may_have_no_tenant",  # system-borne work
    "bug_record": "scope_based",  # public/subscriber visible to all
    "bug_version": "inherits_from_record",
    "source": "scope_based",  # public/subscriber visible to all, like bug_record
    "usage_event": "may_have_no_tenant",  # system-borne spend
    # A quota always belongs to a tenant — there is no system-borne limit — so it needs none of
    # the exceptions jobs and usage events carry.
    "quota": "own_tenant_only",
}


def _tables_with_tenant_id(session) -> set[str]:  # type: ignore[no-untyped-def]
    rows = (
        session.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


def test_every_tenant_scoped_table_is_accounted_for(engine: Engine) -> None:
    """A new table with a tenant_id must be added to EXPECTED deliberately."""
    with tenant_session(engine, None, commit=False) as s:
        actual = _tables_with_tenant_id(s)

    unaccounted = actual - set(EXPECTED)
    assert not unaccounted, (
        f"{sorted(unaccounted)} carry tenant_id but have no declared policy intent. "
        "Add them to EXPECTED and give them a policy."
    )


def test_every_expected_table_has_rls_enabled_and_forced(engine: Engine) -> None:
    """FORCE matters: without it the table owner bypasses the policy silently."""
    with tenant_session(engine, None, commit=False) as s:
        rows = s.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r'"
            )
        ).all()
    state = {r[0]: (r[1], r[2]) for r in rows}

    missing = [t for t in EXPECTED if not all(state.get(t, (False, False)))]
    assert not missing, f"RLS not enabled and forced on: {sorted(missing)}"


def test_every_expected_table_has_at_least_one_policy(engine: Engine) -> None:
    with tenant_session(engine, None, commit=False) as s:
        rows = s.execute(
            text("SELECT tablename, count(*) FROM pg_policies WHERE schemaname='public' GROUP BY 1")
        ).all()
    counts = dict(rows)
    missing = [t for t in EXPECTED if counts.get(t, 0) == 0]
    assert not missing, f"RLS enabled but no policy on: {sorted(missing)} — this denies everything"


def test_tables_needing_writes_have_a_with_check_policy(engine: Engine) -> None:
    """USING governs reads. An INSERT needs WITH CHECK or it is refused outright.

    This was migration 0002's bug: policies existed, reads worked, and every write failed.
    """
    writable = ["team", "membership", "invite", "api_key", "job", "scan", "source", "usage_event"]
    with tenant_session(engine, None, commit=False) as s:
        rows = (
            s.execute(
                text(
                    "SELECT tablename FROM pg_policies "
                    "WHERE schemaname='public' AND with_check IS NOT NULL"
                )
            )
            .scalars()
            .all()
        )
    have = set(rows)
    missing = [t for t in writable if t not in have]
    assert not missing, f"no WITH CHECK policy, so inserts are refused: {sorted(missing)}"
