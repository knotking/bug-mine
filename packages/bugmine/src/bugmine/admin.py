"""`bugmine-admin` — operator commands.

Tenants are provisioned by an operator and users join by invitation (FR-76 to FR-79). There is
no self-serve path, so this is the only way an account comes into existence.

Runs against the database directly rather than through the API, because provisioning the first
tenant is a chicken-and-egg problem: there is no credential yet to authenticate with. In
production it runs as a Cloud Run job inside the VPC, for the same reason migrations do.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from contextlib import contextmanager

from sqlalchemy import select

from bugmine import identity
from bugmine.db import make_engine, tenant_session
from bugmine.models import ApiKey, Membership, PrivacyScope, Role, Team, Tenant, User


def _engine():  # type: ignore[no-untyped-def]
    return make_engine()


@contextmanager
def _for_tenant(slug: str, *, commit: bool = True):  # type: ignore[no-untyped-def]
    """Open a session already scoped to a tenant, resolved by slug.

    Two phases on purpose. `tenant` carries no RLS, so the slug can be resolved anonymously —
    but everything else the operator touches (teams, memberships, keys) keeps strict isolation
    and is invisible without the context. Running these commands on an unscoped session made
    them silently report "no members" and "no such team" against a tenant that had both.
    """
    engine = _engine()
    with tenant_session(engine, None, commit=False) as probe:
        tenant = probe.execute(select(Tenant).where(Tenant.slug == slug)).scalars().first()
        if tenant is None:
            raise SystemExit(f"No tenant with slug {slug!r}. Run `bugmine-admin tenant list`.")
        tenant_id = tenant.id
        tenant_name = tenant.name

    with tenant_session(engine, tenant_id, commit=commit) as session:
        scoped = session.execute(select(Tenant).where(Tenant.id == tenant_id)).scalars().one()
        if scoped.name != tenant_name:  # pragma: no cover - defensive
            raise SystemExit("tenant changed mid-command")
        yield session, scoped


def tenant_create(args: argparse.Namespace) -> int:
    # The tenant id is chosen here so the session can be scoped to it before any row exists.
    # team and membership keep strict RLS, so their inserts need that context.
    new_id = uuid.uuid4()
    with tenant_session(_engine(), new_id) as s:
        tenant, team, invite = identity.provision_tenant(
            s, name=args.name, slug=args.slug, admin_email=args.admin_email
        )
        print(f"tenant   {tenant.slug}  ({tenant.id})")
        print(f"team     {team.name}  ({team.id})")
        print(f"invite   {invite.email}  expires {invite.expires_at:%Y-%m-%d}")
        print()
        print("Send this token to the admin — it is not recoverable afterwards:")
        print(f"  {invite.token}")
    return 0


def tenant_list(_: argparse.Namespace) -> int:
    with tenant_session(_engine(), None, commit=False) as s:
        rows = s.execute(select(Tenant).order_by(Tenant.created_at)).scalars().all()
        if not rows:
            print("No tenants.")
        for t in rows:
            print(f"  {t.slug:<24} {t.name:<28} {t.id}")
    return 0


def invite_create(args: argparse.Namespace) -> int:
    with _for_tenant(args.tenant) as (s, tenant):
        team_id = None
        if args.team:
            team = (
                s.execute(select(Team).where(Team.tenant_id == tenant.id, Team.name == args.team))
                .scalars()
                .first()
            )
            if team is None:
                raise SystemExit(f"No team {args.team!r} in {args.tenant!r}.")
            team_id = team.id

        invite = identity.create_invite(
            s,
            tenant_id=tenant.id,
            email=args.email,
            role=Role.TENANT_ADMIN if args.admin else Role.MEMBER,
            team_id=team_id,
        )
        print(f"invite for {invite.email}, expires {invite.expires_at:%Y-%m-%d}")
        print(f"  {invite.token}")
    return 0


def invite_accept(args: argparse.Namespace) -> int:
    with tenant_session(_engine(), None) as s:
        try:
            membership = identity.accept_invite(s, token=args.token, email=args.email)
        except identity.InviteError as exc:
            print(f"{exc.code}: {exc}", file=sys.stderr)
            return 1
        print(f"joined tenant {membership.tenant_id} as {membership.role.value}")
    return 0


def key_mint(args: argparse.Namespace) -> int:
    with _for_tenant(args.tenant) as (s, tenant):
        user_id: uuid.UUID | None = None
        team_id: uuid.UUID | None = None
        if args.user:
            user = s.execute(select(User).where(User.email == args.user.lower())).scalars().first()
            if user is None:
                raise SystemExit(f"No user {args.user!r}. They must accept an invite first.")
            user_id = user.id
        else:
            team = (
                s.execute(select(Team).where(Team.tenant_id == tenant.id, Team.name == args.team))
                .scalars()
                .first()
            )
            if team is None:
                raise SystemExit(f"No team {args.team!r} in {args.tenant!r}.")
            team_id = team.id

        minted = identity.mint_api_key(
            s, tenant_id=tenant.id, name=args.name, user_id=user_id, team_id=team_id
        )
        print(f"key {minted.prefix}... created")
        print()
        print("Shown once. Store it now:")
        print(f"  {minted.secret}")
    return 0


def key_list(args: argparse.Namespace) -> int:
    with _for_tenant(args.tenant, commit=False) as (s, tenant):
        rows = s.execute(select(ApiKey).where(ApiKey.tenant_id == tenant.id)).scalars().all()
        if not rows:
            print("No keys.")
        for k in rows:
            principal = k.principal_user_id or k.principal_team_id
            state = "revoked" if k.revoked_at else "active"
            print(f"  {k.prefix}...  {k.name:<20} {k.principal_kind.value:<6} {principal}  {state}")
    return 0


def member_list(args: argparse.Namespace) -> int:
    with _for_tenant(args.tenant, commit=False) as (s, tenant):
        rows = s.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == tenant.id)
        ).all()
        if not rows:
            print("No members. Invites may be outstanding.")
        for m, u in rows:
            print(f"  {u.email:<36} {m.role.value}")
    return 0


def job_recent(args: argparse.Namespace) -> int:
    """Recent jobs and why they failed.

    Exists because a worker that converts an exception into a generic HTTP error leaves the
    only useful detail in the job row, and the database is private. Without this, diagnosing a
    live failure means a deploy cycle.
    """
    from bugmine.models.jobs import Job

    with tenant_session(_engine(), None, commit=False) as s:
        rows = (
            s.execute(select(Job).order_by(Job.created_at.desc()).limit(args.limit)).scalars().all()
        )
        if not rows:
            print("No jobs.")
        for j in rows:
            print(f"  {j.created_at:%H:%M:%S}  {j.job_type.value:<14} {j.state.value}")
            if j.failure_reason:
                print(f"      {j.failure_reason}")
    return 0


def record_list(args: argparse.Namespace) -> int:
    """Every catalog record, including ones search hides.

    Deliberately unfiltered by lifecycle state: this is the view an operator needs when
    deciding what to retract, and it is the one place a retracted record should still show.
    """
    from bugmine.catalog.reader import current_version
    from bugmine.models import BugRecord, Component

    with tenant_session(_engine(), None, commit=False) as s:
        stmt = select(BugRecord).order_by(BugRecord.first_seen_at.desc()).limit(args.limit)
        rows = s.execute(stmt).scalars().all()
        if not rows:
            print("No records.")
        for r in rows:
            component = s.get(Component, r.component_id)
            ref = component.canonical_ref if component else "?"
            version = current_version(s, r)
            title = version.title if version else "(no version)"
            applicability = r.applicability or {}
            bounds = (
                f"introduced_in={applicability.get('introduced_in')} "
                f"fixed_in={applicability.get('fixed_in')}"
            )
            print(f"  {r.id}  {r.lifecycle_state.value:<10} {r.bug_type.value:<16} {ref}")
            print(f"      {bounds}")
            print(f"      {title[:100]}")
    return 0


def record_retract(args: argparse.Namespace) -> int:
    """Retract records, by id or by component.

    Retraction rather than deletion: the record's history is why a later reader can tell that
    an absence is deliberate rather than a gap in crawling. Retracted records stop grounding
    findings and stop appearing in search, which is the whole effect wanted here.
    """
    from bugmine.catalog.reader import current_version
    from bugmine.models import BugRecord, Component, LifecycleState

    with tenant_session(_engine(), None) as s:
        stmt = select(BugRecord)
        if args.id:
            stmt = stmt.where(BugRecord.id.in_(args.id))
        elif args.component:
            component = s.execute(
                select(Component).where(Component.canonical_ref == args.component.lower())
            ).scalar_one_or_none()
            if component is None:
                print(f"No component {args.component!r}.")
                return 1
            stmt = stmt.where(BugRecord.component_id == component.id)
        else:
            print("Pass --id or --component.")
            return 2

        rows = s.execute(stmt).scalars().all()
        if not rows:
            print("Nothing matched.")
            return 1
        for r in rows:
            version = current_version(s, r)
            title = version.title if version else str(r.id)
            if r.lifecycle_state is LifecycleState.RETRACTED:
                print(f"  already retracted  {title[:70]}")
                continue
            r.lifecycle_state = LifecycleState.RETRACTED
            print(f"  retracted  {title[:70]}")
    return 0


def source_list(args: argparse.Namespace) -> int:
    """Sources by scope, with duplicates surfaced.

    Duplicates are worth a first-class report rather than a query: a source registered twice is
    crawled twice, which spends the rate-limit budget that decides how much of the catalog gets
    refreshed in an hour.
    """
    from collections import Counter

    from bugmine.models import Source

    with tenant_session(_engine(), None, commit=False) as s:
        rows = s.execute(select(Source)).scalars().all()
        by_scope = Counter(r.privacy_scope.value for r in rows)
        by_url = Counter(r.url for r in rows)
        duplicated = {u: c for u, c in by_url.items() if c > 1}
        print(f"  {len(rows)} source(s) visible without a tenant context")
        for scope, count in sorted(by_scope.items()):
            print(f"    {scope:<10} {count}")
        print(f"  {len(duplicated)} url(s) registered more than once")
        for url, count in list(duplicated.items())[: args.limit]:
            print(f"    x{count}  {url[:88]}")
    return 0


def source_prune(args: argparse.Namespace) -> int:
    """Remove duplicate registrations, keeping the oldest of each URL.

    The oldest rather than the newest: it carries whatever crawl history exists, and dropping it
    would reset `last_success_at` and make a healthy source look like it had never run.

    `--tenant` is required to reach tenant-scoped rows at all. A session with no tenant context
    cannot see them, so a prune run without it reports success over rows it never examined —
    which is the same blindness that let the shared catalog be registered under one tenant in
    the first place.
    """
    from bugmine.models import Source

    def _prune(session) -> tuple[int, int]:  # type: ignore[no-untyped-def]
        # Public first, then oldest. Age alone is the wrong rule when both scopes exist for one
        # URL: only a public source is crawled by the system sweep, and ordering purely by
        # created_at kept 115 tenant rows registered by mistake and deleted every public row
        # behind them — which silently stopped the shared catalog from being crawled at all.
        rows = session.execute(
            select(Source).order_by(
                (Source.privacy_scope == PrivacyScope.PUBLIC).desc(), Source.created_at.asc()
            )
        ).scalars().all()
        seen: set[str] = set()
        removed = 0
        for row in rows:
            if row.url in seen:
                session.delete(row)
                removed += 1
                continue
            seen.add(row.url)
        return removed, len(seen)

    if getattr(args, "tenant", None):
        with _for_tenant(args.tenant) as (session, tenant):
            removed, kept = _prune(session)
        scope = f"tenant {tenant.slug}"
    else:
        with tenant_session(_engine(), None) as session:
            removed, kept = _prune(session)
        scope = "the shared catalog"
    print(f"  removed {removed} source(s) from {scope}; {kept} unique url(s) remain")
    return 0


def source_retire(args: argparse.Namespace) -> int:
    """Remove sources by URL, for the ones that have genuinely gone.

    Distinct from `prune`, which removes duplicate registrations of a URL that still works.
    This is for a URL that answers 404 on every sweep: eight of ours did, all of them repos
    seeded under the wrong org (`boto3/boto3`, `zod/zod`, `esbuild/esbuild`). A permanently
    dead source is not free — it consumes a slot in the rate-limit budget that decides how
    much of the catalog gets refreshed in an hour, and it does so forever, because backoff
    slows a source down without ever concluding it is gone.

    Deliberately by exact URL and not by pattern. Retiring a source is how a component
    silently stops being covered, and coverage loss is invisible in a way that reads as
    "nothing known about this" rather than as an error.
    """
    from bugmine.models import Source

    with tenant_session(_engine(), None) as session:
        rows = session.execute(select(Source).where(Source.url.in_(args.url))).scalars().all()
        found = {r.url for r in rows}
        for row in rows:
            session.delete(row)
            print(f"  retired  {row.url[:88]}")
        for missing in [u for u in args.url if u not in found]:
            print(f"  not registered  {missing[:80]}")
    print(f"  retired {len(rows)} of {len(args.url)} url(s)")
    return 0


def schema_check(args: argparse.Namespace) -> int:
    """Compare the database's alembic revision against the newest migration on disk.

    A migrate job that runs the *previous* image finds nothing to apply and reports success, so
    "the job completed" does not mean "the schema is current". That happened twice: once leaving
    Settings returning 500 on a missing quota table, once leaving /v1/scans returning 500 on a
    missing column. Both looked like a green deploy.
    """
    from pathlib import Path

    from sqlalchemy import text

    versions = Path(__file__).resolve().parents[4] / "migrations" / "versions"
    on_disk = sorted(
        f.stem.split("_")[0] for f in versions.glob("[0-9]*.py") if f.stem[0].isdigit()
    )
    latest = on_disk[-1] if on_disk else None

    with tenant_session(_engine(), None, commit=False) as s:
        row = s.execute(text("SELECT version_num FROM alembic_version")).first()
    current = row[0] if row else None

    print(f"  database: {current}")
    print(f"  on disk:  {latest}")
    if current != latest:
        print("  OUT OF DATE — run the migrate job against the image you just deployed.")
        return 1
    print("  schema is current")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bugmine-admin", description="BugMine operator tools.")
    sub = parser.add_subparsers(dest="group", required=True)

    t = sub.add_parser("tenant").add_subparsers(dest="cmd", required=True)
    tc = t.add_parser("create", help="Provision a tenant and invite its admin")
    tc.add_argument("--name", required=True)
    tc.add_argument("--slug", required=True)
    tc.add_argument("--admin-email", required=True)
    tc.set_defaults(func=tenant_create)
    t.add_parser("list").set_defaults(func=tenant_list)

    i = sub.add_parser("invite").add_subparsers(dest="cmd", required=True)
    ic = i.add_parser("create")
    ic.add_argument("--tenant", required=True)
    ic.add_argument("--email", required=True)
    ic.add_argument("--team")
    ic.add_argument("--admin", action="store_true", help="Grant tenant_admin")
    ic.set_defaults(func=invite_create)
    ia = i.add_parser("accept")
    ia.add_argument("--token", required=True)
    ia.add_argument("--email", required=True)
    ia.set_defaults(func=invite_accept)

    k = sub.add_parser("key").add_subparsers(dest="cmd", required=True)
    km = k.add_parser("mint", help="Mint a key for one user or one team")
    km.add_argument("--tenant", required=True)
    km.add_argument("--name", required=True)
    km.add_argument("--user", help="Bind to a user by email")
    km.add_argument("--team", default="default", help="Bind to a team by name")
    km.set_defaults(func=key_mint)
    kl = k.add_parser("list")
    kl.add_argument("--tenant", required=True)
    kl.set_defaults(func=key_list)

    j = sub.add_parser("job").add_subparsers(dest="cmd", required=True)
    jr = j.add_parser("recent", help="Recent jobs and their failure reasons")
    jr.add_argument("--limit", type=int, default=15)
    jr.set_defaults(func=job_recent)

    src = sub.add_parser("source").add_subparsers(dest="cmd", required=True)
    sl = src.add_parser("list", help="Sources by scope, with duplicates")
    sl.add_argument("--limit", type=int, default=10)
    sl.set_defaults(func=source_list)
    sp = src.add_parser("prune", help="Remove duplicate registrations of the same url")
    sp.add_argument("--tenant", help="Also prune this tenant's own sources (slug)")
    sp.set_defaults(func=source_prune)

    sr = src.add_parser("retire", help="Remove sources whose url is permanently gone")
    sr.add_argument("url", nargs="+", help="Exact url(s) to retire")
    sr.set_defaults(func=source_retire)

    sch = sub.add_parser("schema").add_subparsers(dest="cmd", required=True)
    sc = sch.add_parser("check", help="Is the database schema current?")
    sc.set_defaults(func=schema_check)

    rec = sub.add_parser("record").add_subparsers(dest="cmd", required=True)
    rl = rec.add_parser("list", help="Catalog records, including retracted ones")
    rl.add_argument("--limit", type=int, default=30)
    rl.set_defaults(func=record_list)
    rr = rec.add_parser("retract", help="Retract records by id or component")
    rr.add_argument("--id", nargs="*", default=[])
    rr.add_argument("--component")
    rr.set_defaults(func=record_retract)

    m = sub.add_parser("member").add_subparsers(dest="cmd", required=True)
    ml = m.add_parser("list")
    ml.add_argument("--tenant", required=True)
    ml.set_defaults(func=member_list)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
