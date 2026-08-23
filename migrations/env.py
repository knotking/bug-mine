"""Alembic environment.

The database URL comes from BUGMINE_DATABASE_URL rather than alembic.ini so the same
migrations run against a throwaway container locally and Cloud SQL in GCP without a config
edit.
"""

from __future__ import annotations

from alembic import context
from bugmine.db.session import database_url
from bugmine.models import Base
from sqlalchemy import create_engine, pool

config = context.config
target_metadata = Base.metadata

# The URL is deliberately *not* pushed through config.set_main_option. That path goes via
# configparser, which treats `%` as interpolation syntax — and a URL-encoded password
# containing %3C or %28 raises "invalid interpolation syntax" before a connection is ever
# attempted. Escaping to %% would work and would silently break the moment someone passes a
# raw URL instead. Building the engine directly avoids the class of bug.


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
