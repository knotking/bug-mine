"""Credential and invite handling.

Secrets are stored only as hashes. A leaked database must not yield usable API keys or
redeemable invites — the prefix exists so a key can be identified in a list without the
database holding anything that grants access.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bugmine.models import ApiKey, Invite, Membership, PrincipalKind, Role, Team, Tenant, User

KEY_PREFIX = "bmk_"
PREFIX_LEN = len(KEY_PREFIX) + 8
INVITE_TTL = timedelta(days=7)


def hash_secret(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


@dataclass(frozen=True)
class MintedKey:
    id: uuid.UUID
    secret: str
    prefix: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"MintedKey(id={self.id}, prefix={self.prefix}, secret=<redacted>)"


def mint_api_key(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    name: str,
    user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
) -> MintedKey:
    """Create an API key bound to exactly one principal.

    FR-80: never tenant-wide. Billing and metrics are per team and per user, and the token
    ledger has team_id and user_id columns — a shared key cannot populate them, so per-team
    reporting would be structurally impossible rather than merely unbuilt.
    """
    if (user_id is None) == (team_id is None):
        raise ValueError("a key belongs to exactly one principal: a user or a team")

    secret = KEY_PREFIX + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:40]
    key = ApiKey(
        tenant_id=tenant_id,
        name=name,
        principal_kind=PrincipalKind.USER if user_id else PrincipalKind.TEAM,
        principal_user_id=user_id,
        principal_team_id=team_id,
        prefix=secret[:PREFIX_LEN],
        secret_hash=hash_secret(secret),
    )
    session.add(key)
    session.flush()
    return MintedKey(id=key.id, secret=secret, prefix=key.prefix)


@dataclass(frozen=True)
class MintedInvite:
    id: uuid.UUID
    token: str
    email: str
    expires_at: datetime


def create_invite(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: Role = Role.MEMBER,
    team_id: uuid.UUID | None = None,
    invited_by_user_id: uuid.UUID | None = None,
) -> MintedInvite:
    """FR-77, FR-78. Single-use, expiring, bound to an email address."""
    email = email.strip().lower()
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + INVITE_TTL

    invite = Invite(
        tenant_id=tenant_id,
        email=email,
        role=role,
        team_id=team_id,
        token_hash=hash_secret(token),
        invited_by_user_id=invited_by_user_id,
        expires_at=expires_at,
    )
    session.add(invite)
    session.flush()
    return MintedInvite(id=invite.id, token=token, email=email, expires_at=expires_at)


class InviteError(Exception):
    """Raised with a stable machine-readable code, never a reworded message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def accept_invite(session: Session, *, token: str, email: str) -> Membership:
    """Redeem an invite.

    The email must match. Without that check an intercepted invite link would let anyone join
    a tenant, which is the same exposure FR-79 rules out by forbidding domain-based auto-join.
    """
    email = email.strip().lower()
    invite = (
        session.execute(select(Invite).where(Invite.token_hash == hash_secret(token)))
        .scalars()
        .first()
    )

    if invite is None:
        raise InviteError("invite_not_found", "No such invite.")
    if invite.revoked_at is not None:
        raise InviteError("invite_revoked", "This invite was revoked.")
    if invite.accepted_at is not None:
        raise InviteError("invite_already_used", "This invite has already been used.")
    if invite.expires_at < datetime.now(UTC):
        raise InviteError("invite_expired", "This invite has expired.")
    if invite.email != email:
        raise InviteError("invite_email_mismatch", "This invite was issued to another address.")

    # The invite has now been validated, so the caller is authorised to act for its tenant.
    # Adopting that context here is what lets the membership be written: `membership` keeps
    # strict RLS isolation, and redemption starts with no tenant because the caller could not
    # have known which tenant the token belonged to.
    session.execute(
        text("SELECT set_config('bugmine.tenant_id', :tid, true)"),
        {"tid": str(invite.tenant_id)},
    )

    user = session.execute(select(User).where(User.email == email)).scalars().first()
    if user is None:
        user = User(email=email)
        session.add(user)
        session.flush()

    existing = (
        session.execute(
            select(Membership).where(
                Membership.tenant_id == invite.tenant_id, Membership.user_id == user.id
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise InviteError("already_a_member", "That user is already in this tenant.")

    membership = Membership(
        tenant_id=invite.tenant_id, user_id=user.id, team_id=invite.team_id, role=invite.role
    )
    session.add(membership)
    invite.accepted_at = datetime.now(UTC)
    session.flush()
    return membership


def provision_tenant(
    session: Session, *, name: str, slug: str, admin_email: str
) -> tuple[Tenant, Team, MintedInvite]:
    """FR-76 — the only way a tenant comes into existence. No self-serve path.

    The session must already be scoped to the tenant being created. `team` and `membership`
    keep strict RLS isolation, so their inserts need a tenant context — and provisioning has
    one, because it chose the tenant id.
    """
    tenant = Tenant(id=_current_tenant(session), name=name, slug=slug)
    session.add(tenant)
    session.flush()

    team = Team(tenant_id=tenant.id, name="default")
    session.add(team)
    session.flush()

    invite = create_invite(
        session, tenant_id=tenant.id, email=admin_email, role=Role.TENANT_ADMIN, team_id=team.id
    )
    return tenant, team, invite


def _current_tenant(session: Session) -> uuid.UUID:
    value = session.execute(
        text("SELECT NULLIF(current_setting('bugmine.tenant_id', true), '')")
    ).scalar_one_or_none()
    if not value:
        raise ValueError("provision_tenant needs a tenant-scoped session")
    return uuid.UUID(value)
