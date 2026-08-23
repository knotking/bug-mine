"""Request context.

The one rule every path here enforces: **a request may never name its own tenant.** The tenant
is derived from the credential and nowhere else — not from a path segment, a query parameter,
or a body field. Anything else makes tenant isolation a function of request validation rather
than of authentication.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from bugmine.db import tenant_session
from bugmine.firebase import InvalidToken
from bugmine.models import ApiKey, Membership, PrincipalKind, User

KEY_PREFIX = "bmk_"


@dataclass(frozen=True)
class Principal:
    """FR-80 — a credential acts as exactly one user or one team, never as a tenant."""

    tenant_id: uuid.UUID
    kind: PrincipalKind
    user_id: uuid.UUID | None
    team_id: uuid.UUID | None
    api_key_id: uuid.UUID | None


def _hash_secret(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": {"code": "unauthenticated", "message": "Missing or invalid credentials."}},
    )


def anonymous_session(request: Request) -> Iterator[Session]:
    """The public path. No tenant is set, so RLS makes tenant rows invisible.

    That is a property of the policy rather than something this handler has to remember, which
    is the point of enforcing isolation at the data layer as well as the gateway.
    """
    with tenant_session(request.app.state.engine, None, commit=False) as session:
        yield session


def require_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_bugmine_key: str | None = Header(default=None, alias="X-BugMine-Key"),
) -> Principal:
    """Resolve the caller's principal from an API key.

    Accepted on `Authorization: Bearer` — the documented contract — or on `X-BugMine-Key`.

    The second exists because Cloud Run consumes `Authorization` for its own IAM check, and
    this deployment must be invoked with a Google identity token while the org policy forbids
    anonymous access. Two credentials cannot share one header. The alternate header is a
    workaround for that constraint, not a second way to authenticate: it resolves to the same
    principal through the same lookup, and it becomes unnecessary the moment the service can be
    invoked anonymously.
    """
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()

    token = (x_bugmine_key or "").strip()
    if not token and bearer.startswith(KEY_PREFIX):
        token = bearer

    # A bearer credential that is not an API key is a Firebase ID token — the console's path.
    if not token and bearer:
        return _principal_from_firebase(request, bearer)

    if not token.startswith(KEY_PREFIX):
        raise _unauthorized()

    prefix = token[: len(KEY_PREFIX) + 8]
    # Key lookup runs without a tenant context — RLS would otherwise hide the very row needed
    # to establish which tenant this is.
    with tenant_session(request.app.state.engine, None, commit=False) as session:
        key = session.execute(select(ApiKey).where(ApiKey.prefix == prefix)).scalars().first()
        if key is None or key.revoked_at is not None:
            raise _unauthorized()
        if key.secret_hash != _hash_secret(token):
            raise _unauthorized()
        return Principal(
            tenant_id=key.tenant_id,
            kind=key.principal_kind,
            user_id=key.principal_user_id,
            team_id=key.principal_team_id,
            api_key_id=key.id,
        )


def _principal_from_firebase(request: Request, token: str) -> Principal:
    """Map a verified Firebase token to a principal.

    An unknown UID is refused rather than provisioned. An identity provider will mint an
    account for anyone who completes a sign-up form, so treating a valid token as sufficient
    would reintroduce self-serve through the back door — which the whole tenancy model assumes
    is impossible.
    """
    verifier = getattr(request.app.state, "firebase", None)
    if verifier is None:
        raise _unauthorized()

    try:
        fb = verifier.verify(token)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=401, detail={"error": {"code": exc.code, "message": str(exc)}}
        ) from exc

    with tenant_session(request.app.state.engine, None, commit=False) as session:
        user = (
            session.execute(select(User).where(User.external_subject == fb.uid)).scalars().first()
        )
        if user is None and fb.normalised_email:
            # Adopt a user invited before they ever signed in: the invite created the row, and
            # this is the first token that proves which Firebase identity it belongs to.
            user = (
                session.execute(select(User).where(User.email == fb.normalised_email))
                .scalars()
                .first()
            )
        if user is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "no_account",
                        "message": (
                            "That identity has no BugMine account. Accounts come from invitations."
                        ),
                    }
                },
            )

        membership = (
            session.execute(select(Membership).where(Membership.user_id == user.id))
            .scalars()
            .first()
        )
        if membership is None:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": "no_membership", "message": "No tenant membership."}},
            )

        return Principal(
            tenant_id=membership.tenant_id,
            kind=PrincipalKind.USER,
            user_id=user.id,
            team_id=membership.team_id,
            api_key_id=None,
        )


def tenant_db(
    request: Request, principal: Principal = Depends(require_principal)
) -> Iterator[Session]:
    with tenant_session(request.app.state.engine, principal.tenant_id, commit=False) as session:
        yield session
