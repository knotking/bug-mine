"""Credentials and the invite flow.

Onboarding is invite-only (FR-76 to FR-79), so these tests are mostly about refusal: an invite
that has been used, expired, revoked, or issued to somebody else must not work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from bugmine import identity
from bugmine.db import tenant_session
from bugmine.models import Invite, Role, Team
from sqlalchemy import Engine


def _tenant(engine: Engine) -> uuid.UUID:
    from bugmine.models import Tenant

    tid = uuid.uuid4()
    with tenant_session(engine, None) as s:
        s.add(Tenant(id=tid, name="T", slug=f"t-{tid.hex[:10]}"))
    return tid


class TestApiKeys:
    def test_key_is_bound_to_exactly_one_principal(self, engine: Engine) -> None:
        tid = _tenant(engine)
        with tenant_session(engine, tid) as s:
            team = Team(tenant_id=tid, name="default")
            s.add(team)
            s.flush()

            with pytest.raises(ValueError, match="exactly one principal"):
                identity.mint_api_key(s, tenant_id=tid, name="bad")

            with pytest.raises(ValueError, match="exactly one principal"):
                identity.mint_api_key(
                    s, tenant_id=tid, name="bad", user_id=uuid.uuid4(), team_id=team.id
                )

    def test_secret_is_not_stored(self, engine: Engine) -> None:
        """A leaked database must not yield usable keys."""
        from bugmine.models import ApiKey

        tid = _tenant(engine)
        with tenant_session(engine, tid) as s:
            team = Team(tenant_id=tid, name="default")
            s.add(team)
            s.flush()
            minted = identity.mint_api_key(s, tenant_id=tid, name="ci", team_id=team.id)

        with tenant_session(engine, tid, commit=False) as s:
            row = s.get(ApiKey, minted.id)
            assert row is not None
            assert minted.secret.encode() not in row.secret_hash
            assert row.secret_hash == identity.hash_secret(minted.secret)

    def test_repr_does_not_leak_the_secret(self, engine: Engine) -> None:
        minted = identity.MintedKey(id=uuid.uuid4(), secret="bmk_supersecret", prefix="bmk_super")
        assert "supersecret" not in repr(minted)


class TestInvites:
    def _invite(self, engine: Engine, email: str = "a@example.test"):
        tid = _tenant(engine)
        with tenant_session(engine, None) as s:
            return tid, identity.create_invite(s, tenant_id=tid, email=email)

    def test_accepting_creates_a_membership(self, engine: Engine) -> None:
        _, inv = self._invite(engine)
        with tenant_session(engine, None) as s:
            m = identity.accept_invite(s, token=inv.token, email="a@example.test")
        assert m.role is Role.MEMBER

    def test_an_invite_is_single_use(self, engine: Engine) -> None:
        _, inv = self._invite(engine)
        with tenant_session(engine, None) as s:
            identity.accept_invite(s, token=inv.token, email="a@example.test")
        with tenant_session(engine, None) as s, pytest.raises(identity.InviteError) as exc:
            identity.accept_invite(s, token=inv.token, email="a@example.test")
        assert exc.value.code == "invite_already_used"

    def test_email_must_match(self, engine: Engine) -> None:
        """An intercepted link must not let anybody join."""
        _, inv = self._invite(engine)
        with tenant_session(engine, None) as s, pytest.raises(identity.InviteError) as exc:
            identity.accept_invite(s, token=inv.token, email="attacker@example.test")
        assert exc.value.code == "invite_email_mismatch"

    def test_expired_invites_are_refused(self, engine: Engine) -> None:
        tid, inv = self._invite(engine)
        with tenant_session(engine, None) as s:
            row = s.get(Invite, inv.id)
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with tenant_session(engine, None) as s, pytest.raises(identity.InviteError) as exc:
            identity.accept_invite(s, token=inv.token, email="a@example.test")
        assert exc.value.code == "invite_expired"

    def test_revoked_invites_are_refused(self, engine: Engine) -> None:
        _, inv = self._invite(engine)
        with tenant_session(engine, None) as s:
            row = s.get(Invite, inv.id)
            assert row is not None
            row.revoked_at = datetime.now(UTC)
        with tenant_session(engine, None) as s, pytest.raises(identity.InviteError) as exc:
            identity.accept_invite(s, token=inv.token, email="a@example.test")
        assert exc.value.code == "invite_revoked"

    def test_an_unknown_token_is_refused(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s, pytest.raises(identity.InviteError) as exc:
            identity.accept_invite(s, token="not-a-real-token", email="a@example.test")
        assert exc.value.code == "invite_not_found"

    def test_token_is_not_stored(self, engine: Engine) -> None:
        _, inv = self._invite(engine)
        with tenant_session(engine, None, commit=False) as s:
            row = s.get(Invite, inv.id)
            assert row is not None
            assert row.token_hash == identity.hash_secret(inv.token)


class TestProvisioning:
    def test_provisioning_creates_tenant_team_and_admin_invite(self, engine: Engine) -> None:
        """FR-76 — the only way a tenant comes into existence.

        The session is scoped to the new tenant id before the tenant row exists, because
        `team` keeps strict RLS and its insert needs a tenant context. Provisioning has one:
        it chose the id.
        """
        new_id = uuid.uuid4()
        with tenant_session(engine, new_id) as s:
            tenant, team, invite = identity.provision_tenant(
                s, name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", admin_email="a@acme.test"
            )
        assert tenant.id == new_id
        assert team.tenant_id == tenant.id
        assert invite.email == "a@acme.test"

        with tenant_session(engine, None) as s:
            m = identity.accept_invite(s, token=invite.token, email="a@acme.test")
        assert m.role is Role.TENANT_ADMIN, "the first user must be able to administer"
