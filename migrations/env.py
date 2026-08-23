"""Alembic environment.

The database URL comes from BUGMINE_DATABASE_URL rather than alembic.ini so the same
migrations run against a throwaway container locally and Cloud SQL in GCP without a config
edit.
"""

from __future__ import annotations

import os

from alembic import context
from bugmine.models import Base
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = Base.metadata

_url = os.environ.get("BUGMINE_DATABASE_URL")
if _url:
    config.set_main_option("sqlalchemy.url", _url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
