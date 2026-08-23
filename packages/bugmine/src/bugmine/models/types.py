"""Column type helpers.

`pg_enum` exists because SQLAlchemy's default for a native Postgres ENUM is to persist the
Python member *name* (``LLM_MODEL``), not its value (``llm_model``). Everything else in this
codebase — API payloads, MCP tool arguments, extraction schemas — speaks the value, so the
default would leave the database holding a different vocabulary from the rest of the system,
visible the moment anyone writes raw SQL.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.dialects.postgresql import ENUM


def pg_enum(enum_cls: type[StrEnum], name: str) -> ENUM:
    return ENUM(
        enum_cls,
        name=name,
        values_callable=lambda cls: [member.value for member in cls],
        create_type=False,
    )
