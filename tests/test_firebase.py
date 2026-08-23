"""Firebase token verification.

Every test here is a forgery attempt. A signed JWT looks authentic by construction — what makes
one *ours* is the issuer and audience pinning it to our project, and skipping either check turns
any Firebase project's token into a valid credential here.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from bugmine.firebase import InvalidToken, TokenVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import Engine

PROJECT = "bugmine-dev"
OTHER_PROJECT = "someone-elses-project"


@pytest.fixture(scope="module")
def keypair():  # type: ignore[no-untyped-def]
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Key:
    def __init__(self, public) -> None:  # type: ignore[no-untyped-def]
        self.key = public


class _JWKS:
    """Stands in for PyJWKClient, returning one known key."""

    def __init__(self, public) -> None:  # type: ignore[no-untyped-def]
        self._key = _Key(public)

    def get_signing_key_from_jwt(self, _token: str) -> _Key:
        return self._key


@pytest.fixture
def verifier(keypair):  # type: ignore[no-untyped-def]
    return TokenVerifier(PROJECT, jwks_client=_JWKS(keypair.public_key()))


def _token(keypair, **overrides) -> str:  # type: ignore[no-untyped-def]
    now = int(time.time())
    claims = {
        "sub": f"uid-{uuid.uuid4().hex[:8]}",
        "aud": PROJECT,
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
        "email": "someone@example.test",
        "email_verified": True,
    }
    claims.update(overrides)
    return jwt.encode(claims, keypair, algorithm="RS256")


class TestAccepted:
    def test_a_valid_token_yields_the_uid_and_email(self, verifier, keypair) -> None:  # type: ignore[no-untyped-def]
        user = verifier.verify(_token(keypair, sub="uid-abc", email="A@Example.Test"))
        assert user.uid == "uid-abc"
        assert user.normalised_email == "a@example.test", "email must be normalised for lookup"


class TestRefused:
    def test_a_token_from_another_project(self, verifier, keypair) -> None:  # type: ignore[no-untyped-def]
        """The check that matters most.

        A token signed by Firebase for somebody else's project is cryptographically valid.
        Only the audience distinguishes it from ours.
        """
        token = _token(
            keypair,
            aud=OTHER_PROJECT,
            iss=f"https://securetoken.google.com/{OTHER_PROJECT}",
        )
        with pytest.raises(InvalidToken) as exc:
            verifier.verify(token)
        assert exc.value.code == "token_wrong_project"

    def test_an_expired_token(self, verifier, keypair) -> None:  # type: ignore[no-untyped-def]
        now = int(time.time())
        with pytest.raises(InvalidToken) as exc:
            verifier.verify(_token(keypair, exp=now - 10, iat=now - 3600))
        assert exc.value.code == "token_expired"

    def test_a_token_signed_by_the_wrong_key(self, verifier) -> None:  # type: ignore[no-untyped-def]
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(InvalidToken) as exc:
            verifier.verify(_token(attacker))
        assert exc.value.code == "token_invalid"

    def test_an_unsigned_token(self, verifier) -> None:  # type: ignore[no-untyped-def]
        """`alg: none` is the classic JWT forgery; only RS256 is accepted."""
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": "x",
                "aud": PROJECT,
                "iss": f"https://securetoken.google.com/{PROJECT}",
                "iat": now,
                "exp": now + 60,
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(InvalidToken):
            verifier.verify(token)

    def test_an_auth_time_in_the_future(self, verifier, keypair) -> None:  # type: ignore[no-untyped-def]
        """Claiming to have authenticated later is not evidence of having authenticated."""
        with pytest.raises(InvalidToken) as exc:
            verifier.verify(_token(keypair, auth_time=int(time.time()) + 9999))
        assert exc.value.code == "token_auth_time_invalid"


class TestAccountLookup:
    def test_a_valid_token_for_an_unknown_identity_is_refused(
        self, engine: Engine, verifier, keypair, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        """FR-83 — the rule that keeps invite-only intact.

        Firebase will authenticate anybody who completes a sign-up form. If a verified token
        were enough, self-serve would return through the back door.
        """
        from bugmine.api import create_app
        from fastapi.testclient import TestClient

        app = create_app(engine)
        app.state.firebase = verifier
        client = TestClient(app)

        r = client.get(
            "/v1/whoami", headers={"Authorization": f"Bearer {_token(keypair, sub='nobody')}"}
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "no_account"

    def test_the_token_is_accepted_on_the_bugmine_auth_header(
        self, engine: Engine, verifier, keypair, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        """API Gateway replaces `Authorization` with its own service token before the request
        reaches this process, so a browser cannot deliver a Firebase token on that header. It
        would be discarded and the gateway's token verified instead, failing as
        `token_key_unknown` — an error naming a credential the caller never sent."""
        from bugmine.api import create_app
        from fastapi.testclient import TestClient

        app = create_app(engine)
        app.state.firebase = verifier
        client = TestClient(app)

        r = client.get(
            "/v1/whoami", headers={"X-BugMine-Auth": _token(keypair, sub="nobody")}
        )
        # Reaching `no_account` proves the token was verified: an unknown identity is refused
        # after verification, where a discarded one fails earlier and differently.
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "no_account"

    def test_the_bugmine_auth_header_wins_over_a_gateway_authorization(
        self, engine: Engine, verifier, keypair, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        """The realistic shape in production: the gateway's token is present and irrelevant."""
        from bugmine.api import create_app
        from fastapi.testclient import TestClient

        app = create_app(engine)
        app.state.firebase = verifier
        client = TestClient(app)

        r = client.get(
            "/v1/whoami",
            headers={
                "Authorization": "Bearer gateway-issued-token-not-ours",
                "X-BugMine-Auth": _token(keypair, sub="nobody"),
            },
        )
        assert r.json()["detail"]["error"]["code"] == "no_account"
