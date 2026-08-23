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
from bugmine.models import ApiKey, Membership, Role, Team, Tenant, User


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

    m = sub.add_parser("member").add_subparsers(dest="cmd", required=True)
    ml = m.add_parser("list")
    ml.add_argument("--tenant", required=True)
    ml.set_defaults(func=member_list)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
