"""The BugMine API.

Implements the subset of docs/api/openapi.yaml that the MVP needs. Two structural decisions
carried over from that contract:

- `/v1/public/*` is a separate route tree rather than a scope parameter, so the boundary lives
  in routing where a reviewer can see it rather than in every handler's filter.
- Out-of-scope records return 404, never 403 — a 403 confirms the record exists, which leaks
  the existence of another tenant's data.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi import Query as Q
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from bugmine import sweep as sweep_mod
from bugmine import tasks, urlguard
from bugmine.api import schemas as S
from bugmine.api.deps import Principal, anonymous_session, require_principal, tenant_db
from bugmine.catalog import Query, retrieve
from bugmine.catalog.reader import current_version
from bugmine.db import make_engine, tenant_session
from bugmine.models import (
    ApiKey,
    BugRecord,
    BugType,
    Component,
    Membership,
    PrivacyScope,
    Source,
    SubjectDomain,
    Tenant,
    User,
)

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


logger = logging.getLogger("bugmine.api")

ingest = APIRouter(prefix="/v1/admin/ingest", tags=["admin"])


def _require_operator(
    x_bugmine_operator: str | None = Header(default=None, alias="X-BugMine-Operator"),
) -> None:
    """Operator gate for ingestion triggers.

    A shared secret rather than a role, because the operator role is designed but not built.
    It keeps the trigger off the tenant key path entirely, so no customer credential can start
    a crawl — which matters because a crawl fetches an arbitrary URL with our egress.

    On a **dedicated header, not `Authorization`**: Cloud Run consumes `Authorization` for its
    own IAM check, and this deployment must be invoked with an identity token because the org
    policy forbids anonymous access. Two different credentials cannot share one header, so the
    operator secret needs its own.
    """
    expected = os.environ.get("BUGMINE_OPERATOR_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "operator_disabled", "message": "No operator token set."}},
        )
    supplied = (x_bugmine_operator or "").strip()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthenticated", "message": "Operator token required."}},
        )


@ingest.post("/sweep", dependencies=[Depends(_require_operator)])
def trigger_sweep(request: Request) -> dict[str, object]:
    """Enqueue every source that is due. Called by Cloud Scheduler on a cron.

    The sweep marks each source attempted; the crawl worker marks it succeeded. Keeping those
    separate is what makes staleness detectable — a source that enqueues on schedule and always
    fails would otherwise look permanently healthy, and that failure emits no errors at all.
    """
    with tenant_session(request.app.state.engine, None) as session:
        result = sweep_mod.sweep(
            session, lambda payload: tasks.enqueue(tasks.crawl_target(), payload)
        )
    if result.stale:
        # Absence of success is the only signal a dead source produces. Logged at warning so it
        # can be alerted on without inventing a metric.
        logger.warning("stale crawl sources: %s", ", ".join(result.stale))
    return {
        "considered": result.considered,
        "due": result.due,
        "enqueued": result.enqueued,
        "stale": result.stale,
    }


@ingest.post("/crawl", dependencies=[Depends(_require_operator)])
def trigger_crawl(body: S.CrawlTriggerIn) -> S.JobHandleOut:
    """Enqueue a crawl. Extraction is chained by the crawl worker, but only if content changed.

    Chaining there rather than here is what makes the dedup gate save money: an unchanged
    source costs one HTTP request and no tokens, because nothing downstream is ever enqueued.
    """
    name = tasks.enqueue(
        tasks.crawl_target(),
        {
            "url": body.url,
            "subject_domain": body.subject_domain.value,
            "component_ref": body.component_ref,
            "ecosystem": body.ecosystem,
        },
    )
    return S.JobHandleOut(task=name, queued=True)


console = APIRouter(prefix="/v1", tags=["console"])


@console.get("/whoami", response_model=S.WhoAmIOut)
def whoami(
    session: Session = Depends(tenant_db), principal: Principal = Depends(require_principal)
) -> S.WhoAmIOut:
    """What this credential is. The console's first call — it establishes the session."""
    tenant = session.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found"}})
    return S.WhoAmIOut(
        tenant=S.TenantOut(id=str(tenant.id), name=tenant.name, slug=tenant.slug),
        principal_kind=principal.kind.value,
        principal_id=str(principal.team_id or principal.user_id),
    )


@console.get("/tenant/members", response_model=list[S.MemberOut])
def list_members(session: Session = Depends(tenant_db)) -> list[S.MemberOut]:
    rows = session.execute(select(Membership, User).join(User, User.id == Membership.user_id)).all()
    return [S.MemberOut(email=u.email, role=m.role.value, joined_at=m.joined_at) for m, u in rows]


@console.get("/tenant/api-keys", response_model=list[S.ApiKeyOut])
def list_api_keys(session: Session = Depends(tenant_db)) -> list[S.ApiKeyOut]:
    """Never returns the secret — only the prefix, which is not one."""
    return [
        S.ApiKeyOut(
            id=str(k.id),
            name=k.name,
            prefix=k.prefix,
            principal_kind=k.principal_kind.value,
            created_at=k.created_at,
            revoked=k.revoked_at is not None,
        )
        for k in session.execute(select(ApiKey)).scalars()
    ]


@console.get("/sources", response_model=list[S.SourceOut])
def list_sources(session: Session = Depends(tenant_db)) -> list[S.SourceOut]:
    now = datetime.now(UTC)
    return [
        S.SourceOut(
            id=str(src.id),
            url=src.url,
            subject_domain=src.subject_domain,
            component_ref=src.component_ref,
            ecosystem=src.ecosystem,
            scope=src.privacy_scope,
            interval_minutes=src.interval_minutes,
            enabled=src.enabled,
            last_success_at=src.last_success_at,
            consecutive_failures=src.consecutive_failures,
            stale=src.is_stale(now),
        )
        for src in session.execute(select(Source)).scalars()
    ]


@console.post("/sources", response_model=S.SourceOut, status_code=201)
def create_source(
    body: S.SourceIn,
    session: Session = Depends(tenant_db),
    principal: Principal = Depends(require_principal),
) -> S.SourceOut:
    """Add a tenant crawl source.

    Output is `tenant` scope and never reaches public search.

    The URL is attacker-chosen by definition, so it is validated before it is ever stored —
    rejecting at write time rather than at fetch time means a denied URL never sits in the
    database waiting for a scheduler to pick it up.
    """
    if rejection := urlguard.check(body.url):
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": rejection.code, "message": rejection.message}},
        )

    src = sweep_mod.add_source(
        session,
        url=body.url,
        subject_domain=body.subject_domain,
        component_ref=body.component_ref,
        ecosystem=body.ecosystem,
        interval_minutes=body.interval_minutes,
        privacy_scope=PrivacyScope.TENANT,
        tenant_id=principal.tenant_id,
    )
    return S.SourceOut(
        id=str(src.id),
        url=src.url,
        subject_domain=src.subject_domain,
        component_ref=src.component_ref,
        ecosystem=src.ecosystem,
        scope=src.privacy_scope,
        interval_minutes=src.interval_minutes,
        enabled=src.enabled,
        last_success_at=None,
        consecutive_failures=0,
        stale=False,
    )


def create_app(engine=None) -> FastAPI:  # type: ignore[no-untyped-def]
    app = FastAPI(title="BugMine API", version="1.0.0")
    app.state.engine = engine or make_engine()
    app.include_router(public)
    app.include_router(catalog)
    app.include_router(check)
    app.include_router(ingest)
    app.include_router(console)

    # Served by the API rather than as a separate static host. One fewer deployable, and the
    # console shares an origin with the API so there is no CORS surface to get wrong.
    _console = Path(__file__).parent / "static" / "console.html"

    @app.get("/console", include_in_schema=False, response_class=HTMLResponse)
    def console_page() -> str:
        return _console.read_text()

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
