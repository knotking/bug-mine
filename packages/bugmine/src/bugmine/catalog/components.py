"""Component resolution.

`canonical_ref` is the join key between a catalog record and a scanned dependency. A mismatch
throws no error — it returns an empty result that reads as good news, which is the quietest
possible failure. So resolution happens through an alias registry on both the write and read
paths rather than by comparing free text.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bugmine.models import Component, ComponentAlias, SubjectDomain


def normalise(name: str) -> str:
    return name.strip().lower()


def resolve(
    session: Session, *, subject_domain: SubjectDomain, ref: str, ecosystem: str | None = None
) -> Component | None:
    """Find a component by canonical name or any alias. Case-insensitive."""
    key = normalise(ref)

    stmt = select(Component).where(
        Component.subject_domain == subject_domain,
        func.lower(Component.canonical_ref) == key,
    )
    if ecosystem is not None:
        stmt = stmt.where(Component.ecosystem == ecosystem)
    found = session.execute(stmt).scalars().first()
    if found is not None:
        return found

    alias_stmt = (
        select(Component)
        .join(ComponentAlias, ComponentAlias.component_id == Component.id)
        .where(
            ComponentAlias.alias == key,
            Component.subject_domain == subject_domain,
        )
    )
    return session.execute(alias_stmt).scalars().first()


def get_or_create(
    session: Session,
    *,
    subject_domain: SubjectDomain,
    ref: str,
    ecosystem: str | None = None,
    aliases: list[str] | None = None,
) -> Component:
    existing = resolve(session, subject_domain=subject_domain, ref=ref, ecosystem=ecosystem)
    if existing is not None:
        return existing

    component = Component(
        subject_domain=subject_domain,
        canonical_ref=ref.strip(),
        ecosystem=ecosystem,
    )
    session.add(component)
    session.flush()

    for alias in {normalise(a) for a in (aliases or [])} - {normalise(ref)}:
        session.add(ComponentAlias(component_id=component.id, alias=alias))
    session.flush()
    return component
