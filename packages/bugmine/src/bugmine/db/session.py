"""Database sessions, scoped to a tenant.

Row-level security policies read `bugmine.tenant_id` from the Postgres session. Every session
therefore has to declare which tenant it is acting for — or declare that it is acting for none,
which is the anonymous public path.

`SET LOCAL` rather than `SET`: the setting is scoped to the transaction, so a pooled connection
handed to the next request cannot inherit the previous request's tenant. With plain `SET` a
connection returned to the pool would keep the value, and the resulting cross-tenant read would
be intermittent and load-dependent — the worst kind to reproduce.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote_plus

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    """Resolve the connection URL.

    A full `BUGMINE_DATABASE_URL` wins, which suits local development. In Cloud Run the parts
    arrive separately so that only the password comes from Secret Manager — a whole URL in an
    environment variable puts the credential into anything that dumps the environment, which
    includes most crash handlers.
    """
    if url := os.environ.get("BUGMINE_DATABASE_URL"):
        return url

    host = os.environ.get("BUGMINE_DB_HOST")
    if not host:
        raise RuntimeError("Set BUGMINE_DATABASE_URL, or BUGMINE_DB_HOST and friends.")

    user = os.environ.get("BUGMINE_DB_USER", "bugmine_app")
    password = quote_plus(os.environ.get("BUGMINE_DB_PASSWORD", ""))
    name = os.environ.get("BUGMINE_DB_NAME", "bugmine")
    port = os.environ.get("BUGMINE_DB_PORT", "5432")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


def make_engine(url: str | None = None, **kwargs: object) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True, **kwargs)  # type: ignore[arg-type]


@contextmanager
def tenant_session(
    engine: Engine, tenant_id: uuid.UUID | None, *, commit: bool = True
) -> Iterator[Session]:
    """Open a session bound to one tenant, or to none.

    `tenant_id=None` is the anonymous path: RLS then matches no tenant rows, so only `public`
    and `subscriber` catalog records are visible. That is a positive property of the policy
    rather than something handlers have to remember.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        session.execute(
            text("SELECT set_config('bugmine.tenant_id', :tid, true)"),
            {"tid": str(tenant_id) if tenant_id else ""},
        )
        yield session
        if commit:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
