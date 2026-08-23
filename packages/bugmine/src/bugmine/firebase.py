"""Firebase ID token verification.

BugMine never sees a password. Firebase holds them, and hands the browser a signed JWT; this
module checks the signature and tells the rest of the system which Firebase UID it belongs to.

Verification is done here rather than at the gateway on purpose. Mapping a token to a principal
needs the database, and a gateway that could approve requests without one would be a second
authorisation path to keep correct.

The checks below are the ones that matter, and skipping any of them turns a signed token into a
usable forgery from a different project:

- **Signature**, against Google's rotating public keys, matched by the token's `kid`.
- **Issuer and audience**, which pin the token to *our* Firebase project. A validly signed
  token from somebody else's project is otherwise indistinguishable from one of ours.
- **Expiry**, and `auth_time` — a token issued before the user actually authenticated is not
  evidence that they did.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

CERT_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
)
JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
ISSUER_PREFIX = "https://securetoken.google.com/"


class InvalidToken(Exception):
    """Raised with a stable code. Callers branch on the code, never the message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FirebaseUser:
    uid: str
    email: str | None
    email_verified: bool
    auth_time: int

    @property
    def normalised_email(self) -> str | None:
        return self.email.strip().lower() if self.email else None


class TokenVerifier:
    """Verifies Firebase ID tokens for one project.

    The JWKS client caches keys and refreshes on an unknown `kid`, so key rotation does not
    require a redeploy — but it does mean a first request after rotation pays a fetch.
    """

    def __init__(self, project_id: str, *, jwks_client: Any | None = None) -> None:
        self.project_id = project_id
        self._jwks = jwks_client or PyJWKClient(JWKS_URL, cache_keys=True)

    def verify(self, token: str, *, now: int | None = None) -> FirebaseUser:
        now = now or int(time.time())
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
        except Exception as exc:
            raise InvalidToken("token_key_unknown", "Token signing key is not recognised.") from exc

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.project_id,
                issuer=f"{ISSUER_PREFIX}{self.project_id}",
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise InvalidToken("token_expired", "Sign-in has expired.") from exc
        except (jwt.InvalidAudienceError, jwt.InvalidIssuerError) as exc:
            # A validly signed token from another Firebase project. Both claims pin the token
            # to a project, and either mismatching means the same thing — without these checks
            # any Firebase project's token would be accepted here.
            raise InvalidToken("token_wrong_project", "Token is for a different project.") from exc
        except jwt.InvalidTokenError as exc:
            raise InvalidToken("token_invalid", "Token could not be verified.") from exc

        if not claims.get("sub"):
            raise InvalidToken("token_no_subject", "Token carries no subject.")

        auth_time = int(claims.get("auth_time", claims["iat"]))
        if auth_time > now + 60:
            # Claiming to have authenticated in the future is not evidence of authenticating.
            raise InvalidToken("token_auth_time_invalid", "Token authentication time is invalid.")

        return FirebaseUser(
            uid=claims["sub"],
            email=claims.get("email"),
            email_verified=bool(claims.get("email_verified", False)),
            auth_time=auth_time,
        )


def create_user(api_key: str, email: str, password: str, *, timeout: float = 15.0) -> str:
    """Create a Firebase user, returning its UID.

    Used during invite acceptance. Deliberately before the local rows are written: a local
    failure then leaves an orphaned Firebase account that the next attempt can adopt, whereas
    the reverse leaves a member who can never sign in and looks fine until they try.
    """
    with httpx.Client(timeout=timeout) as http:
        r = http.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}",
            json={"email": email, "password": password, "returnSecureToken": False},
        )
    if r.status_code >= 400:
        detail = r.json().get("error", {}).get("message", "UNKNOWN")
        if detail == "EMAIL_EXISTS":
            raise InvalidToken("email_exists", "That email already has an account.")
        raise InvalidToken("firebase_signup_failed", f"Firebase rejected the sign-up: {detail}")
    return r.json()["localId"]
