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
from bugmine.models import ApiKey, PrincipalKind

KEY_PREFIX = "bmk_"


@dataclass(frozen=True)
class Principal:
    """FR-80 — a credential acts as exactly one user or one team, never as a tenant."""

    tenant_id: uuid.UUID
    kind: PrincipalKind
    user_id: uuid.UUID | None
    team_id: uuid.UUID | None
    api_key_id: uuid.UUID


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
    request: Request, authorization: str | None = Header(default=None)
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized()
    token = authorization.split(" ", 1)[1].strip()
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


def tenant_db(
    request: Request, principal: Principal = Depends(require_principal)
) -> Iterator[Session]:
    with tenant_session(request.app.state.engine, principal.tenant_id, commit=False) as session:
        yield session
