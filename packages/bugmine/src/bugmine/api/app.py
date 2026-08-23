"""The BugMine API.

Implements the subset of docs/api/openapi.yaml that the MVP needs. Two structural decisions
carried over from that contract:

- `/v1/public/*` is a separate route tree rather than a scope parameter, so the boundary lives
  in routing where a reviewer can see it rather than in every handler's filter.
- Out-of-scope records return 404, never 403 — a 403 confirms the record exists, which leaks
  the existence of another tenant's data.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi import Query as Q
from sqlalchemy import select
from sqlalchemy.orm import Session

from bugmine.api import schemas as S
from bugmine.api.deps import Principal, anonymous_session, require_principal, tenant_db
from bugmine.catalog import Query, retrieve
from bugmine.catalog.reader import current_version
from bugmine.db import make_engine
from bugmine.models import BugRecord, BugType, Component, PrivacyScope, SubjectDomain

ECOSYSTEM_DOMAIN = {
    "pypi": SubjectDomain.REPO_LIBRARY,
    "npm": SubjectDomain.REPO_LIBRARY,
}


def _bug_out(session: Session, record: BugRecord) -> S.BugOut | None:
    version = current_version(session, record)
    if version is None:
        return None
    component = session.get(Component, record.component_id)
    assert component is not None
    return S.BugOut(
        id=str(record.id),
        scope=record.privacy_scope,
        origin=record.origin,
        lifecycle_state=record.lifecycle_state,
        bug_type=record.bug_type,
        subject_domain=record.subject_domain,
        component=S.ComponentOut(
            ref=component.canonical_ref,
            subject_domain=component.subject_domain,
            ecosystem=component.ecosystem,
        ),
        applicability=record.applicability,
        title=version.title,
        description=version.description,
        evidence_url=version.evidence_url,
        observed_at=version.observed_at,
    )


public = APIRouter(prefix="/v1/public", tags=["public"])


@public.get("/bugs/search", response_model=list[S.BugOut])
def search_public_bugs(
    session: Session = Depends(anonymous_session),
    q: str | None = None,
    subject_domain: SubjectDomain | None = None,
    bug_type: BugType | None = None,
    limit: int = Q(default=50, ge=1, le=200),
) -> list[S.BugOut]:
    stmt = select(BugRecord).where(BugRecord.privacy_scope == PrivacyScope.PUBLIC)
    if subject_domain:
        stmt = stmt.where(BugRecord.subject_domain == subject_domain)
    if bug_type:
        stmt = stmt.where(BugRecord.bug_type == bug_type)
    if q:
        stmt = stmt.join(Component, Component.id == BugRecord.component_id).where(
            Component.canonical_ref.ilike(f"%{q}%")
        )
    out = [_bug_out(session, r) for r in session.execute(stmt.limit(limit)).scalars()]
    return [b for b in out if b is not None]


catalog = APIRouter(prefix="/v1", tags=["catalog"])


@catalog.get("/bugs/{bug_id}", response_model=S.BugOut)
def get_bug(bug_id: str, session: Session = Depends(tenant_db)) -> S.BugOut:
    record = session.get(BugRecord, bug_id)
    if record is None:
        # Absent and out-of-scope are deliberately indistinguishable.
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "not_found", "message": "No such bug."}},
        )
    out = _bug_out(session, record)
    if out is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "not_found", "message": "No such bug."}},
        )
    return out


check = APIRouter(prefix="/v1/check", tags=["check"])


@check.post("/dependencies", response_model=S.DependencyCheckOut)
def check_dependencies(
    body: S.DependencyCheckIn,
    session: Session = Depends(tenant_db),
    principal: Principal = Depends(require_principal),
) -> S.DependencyCheckOut:
    """The client-side scanning path.

    Only component names and versions cross the network. The dependency graph is resolved on
    the developer's machine, so source code never leaves it — which is what makes this a
    better fit for an IDE than a dashboard could be.
    """
    now = datetime.now(UTC)
    matches: list[S.DependencyMatchOut] = []
    not_covered: list[S.NotCoveredOut] = []

    for dep in body.dependencies:
        domain = ECOSYSTEM_DOMAIN.get(dep.ecosystem)
        if domain is None:
            not_covered.append(
                S.NotCoveredOut(
                    ecosystem=dep.ecosystem,
                    name=dep.name,
                    version=dep.version,
                    reason="ecosystem_unsupported",
                )
            )
            continue

        found, missing = retrieve(
            session,
            Query(domain, dep.name, version=dep.version, ecosystem=dep.ecosystem),
            bug_types=body.bug_types,
            at=now,
        )
        if missing is not None:
            not_covered.append(
                S.NotCoveredOut(
                    ecosystem=dep.ecosystem,
                    name=dep.name,
                    version=dep.version,
                    reason="component_not_in_catalog",
                )
            )
            continue

        for m in found:
            matches.append(
                S.DependencyMatchOut(
                    dependency=dep,
                    bug_id=str(m.record.id),
                    bug_type=m.record.bug_type,
                    title=m.version.title,
                    version_confirmed=m.version_confirmed,
                    match_note=m.result.reason,
                    citation=S.CitationOut(
                        bug_version_id=str(m.version.id),
                        bug_id=str(m.record.id),
                        evidence_url=m.version.evidence_url,
                    ),
                )
            )

    return S.DependencyCheckOut(matches=matches, not_covered=not_covered, checked_at=now)


def create_app(engine=None) -> FastAPI:  # type: ignore[no-untyped-def]
    app = FastAPI(title="BugMine API", version="1.0.0")
    app.state.engine = engine or make_engine()
    app.include_router(public)
    app.include_router(catalog)
    app.include_router(check)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
