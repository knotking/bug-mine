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
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi import Query as Q
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bugmine import metering, tasks, urlguard
from bugmine import scan as scan_mod
from bugmine import sweep as sweep_mod
from bugmine.api import schemas as S
from bugmine.api.deps import (
    Principal,
    anonymous_session,
    require_principal,
    tenant_db,
    tenant_db_write,
)
from bugmine.catalog import Query, retrieve
from bugmine.catalog.reader import current_version
from bugmine.db import make_engine, tenant_session
from bugmine.firebase import TokenVerifier
from bugmine.identity import mint_api_key
from bugmine.models import (
    VISIBLE_LIFECYCLE_STATES,
    ApiKey,
    BugRecord,
    BugType,
    Component,
    Finding,
    Job,
    JobState,
    JobType,
    Membership,
    PrivacyScope,
    Quota,
    Scan,
    Source,
    SubjectDomain,
    Team,
    Tenant,
    User,
)
from bugmine.models.metering import CostBearer, UsageEvent

# Kept in step with bugmine.scan.ECOSYSTEM_DOMAIN: an ecosystem missing from either is
# reported "not covered" regardless of what the catalog holds.
ECOSYSTEM_DOMAIN = dict(scan_mod.ECOSYSTEM_DOMAIN)


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
    stmt = select(BugRecord).where(
        BugRecord.privacy_scope == PrivacyScope.PUBLIC,
        BugRecord.lifecycle_state.in_(VISIBLE_LIFECYCLE_STATES),
    )
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


@public.get("/stats")
def public_stats(session: Session = Depends(anonymous_session)) -> dict[str, object]:
    """Public catalog coverage, for anyone — no credential.

    The landing page shows what the catalog actually holds rather than claiming a number, and
    the same route lets a prospective user check coverage of something they depend on before
    asking for an account. Public scope only, so nothing here is a tenant's.
    """
    scope = (
        BugRecord.privacy_scope == PrivacyScope.PUBLIC,
        BugRecord.lifecycle_state.in_(VISIBLE_LIFECYCLE_STATES),
    )
    by_type = dict(
        session.execute(
            select(BugRecord.bug_type, func.count(BugRecord.id))
            .where(*scope)
            .group_by(BugRecord.bug_type)
        ).all()
    )
    records = int(session.execute(select(func.count(BugRecord.id)).where(*scope)).scalar_one())
    components = int(
        session.execute(
            select(func.count(func.distinct(BugRecord.component_id))).where(*scope)
        ).scalar_one()
    )
    return {
        "records": records,
        "components": components,
        "by_type": {k.value: int(v) for k, v in by_type.items()},
        # The split that carries the argument: what CVE-shaped tooling does not report.
        "beyond_security": records - int(by_type.get(BugType.SECURITY, 0)),
    }


@public.get("/components")
def public_components(
    session: Session = Depends(anonymous_session),
    limit: int = Q(default=60, ge=1, le=200),
) -> list[dict[str, object]]:
    """What the catalog actually covers, most-covered first.

    Coverage is the honest answer to "is BugMine any use to me" — a visitor can see whether the
    things they depend on are in here before asking for an account, rather than being told a
    total and left to guess whether it includes anything of theirs.
    """
    rows = session.execute(
        select(
            Component.canonical_ref,
            Component.ecosystem,
            BugRecord.subject_domain,
            func.count(BugRecord.id),
        )
        .join(BugRecord, BugRecord.component_id == Component.id)
        .where(
            BugRecord.privacy_scope == PrivacyScope.PUBLIC,
            BugRecord.lifecycle_state.in_(VISIBLE_LIFECYCLE_STATES),
        )
        .group_by(Component.canonical_ref, Component.ecosystem, BugRecord.subject_domain)
        .order_by(func.count(BugRecord.id).desc())
        .limit(limit)
    ).all()
    return [
        {"ref": ref, "ecosystem": eco, "subject_domain": domain.value, "records": int(n)}
        for ref, eco, domain, n in rows
    ]


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


@ingest.post("/osv", dependencies=[Depends(_require_operator)])
def trigger_osv(request: Request, limit: int = Q(default=250, ge=1, le=2000)) -> dict[str, object]:
    """Ingest OSV advisories for every component already in the catalog.

    Driven from the components we hold rather than from OSV's full export: the export is tens of
    thousands of advisories for packages nobody here depends on, and coverage of what we already
    track is worth more than volume for its own sake.
    """
    with tenant_session(request.app.state.engine, None, commit=False) as session:
        rows = session.execute(
            select(Component.canonical_ref, Component.ecosystem)
            .where(Component.ecosystem.isnot(None))
            .distinct()
            .limit(limit)
        ).all()

    enqueued = 0
    for ref, ecosystem in rows:
        if ecosystem not in scan_mod.ECOSYSTEM_DOMAIN:
            continue
        try:
            tasks.enqueue(tasks.osv_target(), {"ecosystem": ecosystem, "name": ref})
            enqueued += 1
        except Exception:
            logger.exception("could not enqueue OSV for %s/%s", ecosystem, ref)
    return {"components": len(rows), "enqueued": enqueued}


@ingest.post("/sources", dependencies=[Depends(_require_operator)], status_code=201)
def add_system_source(body: S.SourceIn, request: Request) -> S.SourceOut:
    """Register a source for the **shared** catalog.

    Distinct from the tenant route, and the distinction is not cosmetic. A tenant's own source
    is private to them and swept under their scope; the public catalog is system-owned, and a
    system sweep — which runs with no tenant context — cannot see tenant rows at all. Adding a
    shared source through the console endpoint therefore registers it successfully and it is
    never crawled, with nothing to say why.
    """
    if rejection := urlguard.check(body.url):
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": rejection.code, "message": rejection.message}},
        )
    with tenant_session(request.app.state.engine, None) as session:
        src = sweep_mod.add_source(
            session,
            url=body.url,
            subject_domain=body.subject_domain,
            component_ref=body.component_ref,
            ecosystem=body.ecosystem,
            privacy_scope=PrivacyScope.PUBLIC,
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


@ingest.post("/extract", dependencies=[Depends(_require_operator)])
def trigger_extract(body: S.ExtractTriggerIn) -> S.JobHandleOut:
    """Re-extract an artifact already in storage.

    The crawl worker deliberately chains extraction only on changed content, so a corrected
    extractor would otherwise never see anything already crawled — re-crawling fetches the same
    bytes and enqueues nothing. Operator-only, because it spends tokens on demand.
    """
    name = tasks.enqueue(
        tasks.extract_target(),
        {
            "artifact_uri": body.artifact_uri,
            "subject_domain": body.subject_domain.value,
            "component_ref": body.component_ref,
            "ecosystem": body.ecosystem,
            "mode": body.mode,
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


@console.get("/catalog/search", response_model=list[S.BugOut])
def console_search(
    session: Session = Depends(tenant_db),
    q: str | None = None,
    bug_type: BugType | None = None,
    limit: int = Q(default=50, ge=1, le=200),
) -> list[S.BugOut]:
    """Search everything this tenant may see — public, subscriber, and its own records.

    Distinct from the public route: that one is hard-wired to public scope for anonymous
    callers, this one relies on RLS to widen the result to what the principal is entitled to.
    """
    stmt = select(BugRecord).where(BugRecord.lifecycle_state.in_(VISIBLE_LIFECYCLE_STATES))
    if bug_type:
        stmt = stmt.where(BugRecord.bug_type == bug_type)
    if q:
        stmt = stmt.join(Component, Component.id == BugRecord.component_id).where(
            Component.canonical_ref.ilike(f"%{q}%")
        )
    out = [_bug_out(session, r) for r in session.execute(stmt.limit(limit)).scalars()]
    return [b for b in out if b is not None]


@console.get("/catalog/summary")
def console_summary(session: Session = Depends(tenant_db)) -> dict[str, object]:
    """What the catalog actually knows.

    Coverage is reported alongside record counts because an empty result and an uncovered
    component are different answers, and a console that showed only totals would let a thin
    catalog look like a clean bill of health.
    """
    # Counted over visible records only, and for the same reason search is: a retracted record
    # is one we have disowned. Counting it here while search omits it would have the console
    # contradict itself on one screen, and would inflate coverage with records nobody may see.
    visible = BugRecord.lifecycle_state.in_(VISIBLE_LIFECYCLE_STATES)
    by_type = dict(
        session.execute(
            select(BugRecord.bug_type, func.count(BugRecord.id))
            .where(visible)
            .group_by(BugRecord.bug_type)
        ).all()
    )
    # Components are counted through their records, not from the registry: a component whose
    # every record has been retracted is not covered, and the registry keeps the row.
    components = session.execute(
        select(func.count(func.distinct(BugRecord.component_id))).where(visible)
    ).scalar_one()
    return {
        "records": int(
            session.execute(select(func.count(BugRecord.id)).where(visible)).scalar_one()
        ),
        "components": int(components),
        "by_type": {k.value: int(v) for k, v in by_type.items()},
    }


@console.get("/tenant/members", response_model=list[S.MemberOut])
def list_members(session: Session = Depends(tenant_db)) -> list[S.MemberOut]:
    rows = session.execute(select(Membership, User).join(User, User.id == Membership.user_id)).all()
    return [S.MemberOut(email=u.email, role=m.role.value, joined_at=m.joined_at) for m, u in rows]


@console.post("/scans", response_model=S.ScanOut, status_code=202)
def start_scan(
    body: S.StartScanIn,
    session: Session = Depends(tenant_db_write),
    principal: Principal = Depends(require_principal),
) -> S.ScanOut:
    """Queue a repository scan.

    The URL is validated here as well as in the fetch worker. Rejecting at submission means a
    denied target never reaches a queue, so it cannot be retried by the dispatcher long after
    the caller was told no.
    """
    if rejection := urlguard.check(body.repo_url):
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": rejection.code, "message": rejection.message}},
        )

    job = Job(
        tenant_id=principal.tenant_id,
        job_type=JobType.SCAN_FETCH,
        state=JobState.QUEUED,
        payload={"repo_url": body.repo_url, "ref": body.ref},
    )
    session.add(job)
    session.flush()
    scan = Scan(job_id=job.id, tenant_id=principal.tenant_id, repo_ref=body.repo_url)
    session.add(scan)
    session.flush()

    tasks.enqueue(
        tasks.scan_fetch_target(),
        {
            "repo_url": body.repo_url,
            "ref": body.ref,
            "tenant_id": str(principal.tenant_id),
            "scan_id": str(scan.id),
        },
    )
    return S.ScanOut(
        id=str(scan.id),
        repo_ref=scan.repo_ref,
        commit_sha=None,
        created_at=scan.created_at,
        grounding_complete=scan.grounding_complete,
        uncovered_components=scan.uncovered_components,
        unresolved_manifests=0,
        suppressed_unreachable=0,
        dependencies_scanned=0,
        findings=0,
    )


@console.get("/scans", response_model=list[S.ScanOut])
def list_scans(
    session: Session = Depends(tenant_db),
    limit: int = Q(default=25, ge=1, le=200),
) -> list[S.ScanOut]:
    scans = session.execute(
        select(Scan).order_by(Scan.created_at.desc()).limit(limit)
    ).scalars().all()
    counts = dict(
        session.execute(
            select(Finding.scan_id, func.count(Finding.id)).group_by(Finding.scan_id)
        ).all()
    )
    return [
        S.ScanOut(
            id=str(s.id),
            repo_ref=s.repo_ref,
            commit_sha=s.commit_sha,
            created_at=s.created_at,
            grounding_complete=s.grounding_complete,
            uncovered_components=s.uncovered_components,
            unresolved_manifests=s.unresolved_manifests,
            suppressed_unreachable=s.suppressed_unreachable,
            dependencies_scanned=s.dependencies_scanned,
            findings=int(counts.get(s.id, 0)),
        )
        for s in scans
    ]


@console.get("/scans/{scan_id}/findings", response_model=list[S.FindingOut])
def scan_findings(scan_id: str, session: Session = Depends(tenant_db)) -> list[S.FindingOut]:
    """Findings for one scan, each with the record that grounds it.

    Absent and out-of-scope are indistinguishable: RLS hides another tenant's scan, and an
    empty list is the same answer a scan with no findings gives.
    """
    rows = session.execute(select(Finding).where(Finding.scan_id == scan_id)).scalars().all()
    components = {c.id: c.canonical_ref for c in session.execute(select(Component)).scalars()}
    return [
        S.FindingOut(
            id=str(f.id),
            component=components.get(f.component_id, "unknown"),
            bug_type=f.bug_type,
            title=f.title,
            detail=f.detail,
            reachable=f.reachable,
            confidence=f.confidence,
            version_confirmed=f.version_confirmed,
            citations=[str(c.bug_version_id) for c in f.citations],
        )
        for f in rows
    ]


@console.get("/usage", response_model=S.UsageOut)
def tenant_usage(
    session: Session = Depends(tenant_db),
    days: int = Q(default=30, ge=1, le=365),
) -> S.UsageOut:
    """Token and cost accounting for this tenant, split by team, user and purpose.

    The ledger has carried this since the metering work; nothing exposed it, so the accounting
    asked for at system, team and user level existed and was invisible. RLS scopes the rows to
    the caller's tenant, so this cannot report another tenant's spend even if the query forgot
    to filter — but system spend is excluded explicitly below rather than left to that.
    """
    until = datetime.now(UTC)
    since = until - timedelta(days=days)

    # Tenant-borne only. System spend is the cost of building the catalog at all; it does not
    # vary with a tenant's usage and showing it here would read as a bill they owe.
    scope = (
        UsageEvent.occurred_at >= since,
        UsageEvent.cost_bearer == CostBearer.TENANT,
    )

    totals = session.execute(
        select(
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cost_micros), 0),
        ).where(*scope)
    ).one()

    def breakdown(group_column, label_for) -> list[S.UsageLineOut]:  # type: ignore[no-untyped-def]
        rows = session.execute(
            select(
                group_column,
                UsageEvent.purpose,
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0),
                func.count(UsageEvent.id),
                # Failed calls are billed and must be visible: a retry loop that never succeeds
                # spends real money, and a total that hid it would look like idleness.
                func.count(UsageEvent.id).filter(~UsageEvent.succeeded),
                func.coalesce(func.sum(UsageEvent.cost_micros), 0),
            )
            .where(*scope)
            .group_by(group_column, UsageEvent.purpose)
            .order_by(func.sum(UsageEvent.cost_micros).desc())
        ).all()
        return [
            S.UsageLineOut(
                label=label_for(key),
                purpose=purpose.value,
                input_tokens=int(inp),
                output_tokens=int(out),
                cached_input_tokens=int(cached),
                calls=int(calls),
                failed_calls=int(failed),
                cost_micros=int(cost),
            )
            for key, purpose, inp, out, cached, calls, failed, cost in rows
        ]

    team_names = {t.id: t.name for t in session.execute(select(Team)).scalars()}
    user_emails = {u.id: u.email for u in session.execute(select(User)).scalars()}

    return S.UsageOut(
        since=since,
        until=until,
        input_tokens=int(totals[0]),
        output_tokens=int(totals[1]),
        cached_input_tokens=int(totals[2]),
        cost_micros=int(totals[3]),
        by_team=breakdown(UsageEvent.team_id, lambda k: team_names.get(k, "unattributed")),
        by_user=breakdown(UsageEvent.user_id, lambda k: user_emails.get(k, "unattributed")),
        by_purpose=breakdown(UsageEvent.purpose, lambda k: k.value),
    )


@console.get("/quotas", response_model=list[S.QuotaOut])
def list_quotas(session: Session = Depends(tenant_db)) -> list[S.QuotaOut]:
    """Every limit set for this tenant, with what has been spent against it this month."""
    team_names = {t.id: t.name for t in session.execute(select(Team)).scalars()}
    user_emails = {u.id: u.email for u in session.execute(select(User)).scalars()}

    out: list[S.QuotaOut] = []
    for q in session.execute(select(Quota)).scalars():
        status = metering.quota_status(
            session, tenant_id=q.tenant_id, team_id=q.team_id, user_id=q.user_id
        )
        if q.user_id is not None:
            scope, label = "user", user_emails.get(q.user_id, "unknown user")
        elif q.team_id is not None:
            scope, label = "team", team_names.get(q.team_id, "unknown team")
        else:
            scope, label = "tenant", "Everyone"
        out.append(
            S.QuotaOut(
                id=str(q.id),
                scope=scope,
                team_id=str(q.team_id) if q.team_id else None,
                user_id=str(q.user_id) if q.user_id else None,
                label=label,
                limit_micros=q.limit_micros,
                limit_tokens=q.limit_tokens,
                spent_micros=status.spent_micros,
                spent_tokens=status.spent_tokens,
            )
        )
    return out


@console.put("/quotas", response_model=S.QuotaOut)
def set_quota(
    body: S.SetQuotaIn,
    session: Session = Depends(tenant_db_write),
    principal: Principal = Depends(require_principal),
) -> S.QuotaOut:
    """Create or update one principal's monthly limit.

    Upsert rather than create: one limit per principal is a database constraint, so a second
    POST would be an error the caller has to distinguish from a real failure.
    """
    if body.team_id is not None and body.user_id is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "ambiguous_scope",
                    "message": "A quota narrows to a team or a user, not both.",
                }
            },
        )
    team_id = uuid.UUID(body.team_id) if body.team_id else None
    user_id = uuid.UUID(body.user_id) if body.user_id else None

    existing = session.execute(
        select(Quota).where(
            Quota.tenant_id == principal.tenant_id,
            Quota.team_id.is_(team_id) if team_id is None else Quota.team_id == team_id,
            Quota.user_id.is_(user_id) if user_id is None else Quota.user_id == user_id,
        )
    ).scalars().first()

    if existing is None:
        existing = Quota(tenant_id=principal.tenant_id, team_id=team_id, user_id=user_id)
        session.add(existing)
    existing.limit_micros = body.limit_micros
    existing.limit_tokens = body.limit_tokens
    session.flush()

    status = metering.quota_status(
        session, tenant_id=principal.tenant_id, team_id=team_id, user_id=user_id
    )
    scope = "user" if user_id else ("team" if team_id else "tenant")
    return S.QuotaOut(
        id=str(existing.id),
        scope=scope,
        team_id=body.team_id,
        user_id=body.user_id,
        label={"user": "user", "team": "team", "tenant": "Everyone"}[scope],
        limit_micros=existing.limit_micros,
        limit_tokens=existing.limit_tokens,
        spent_micros=status.spent_micros,
        spent_tokens=status.spent_tokens,
    )


@console.post("/tenant/api-keys", response_model=S.MintedKeyOut, status_code=201)
def mint_key(
    body: S.MintKeyIn,
    session: Session = Depends(tenant_db_write),
    principal: Principal = Depends(require_principal),
) -> S.MintedKeyOut:
    """Mint a key for one team or one user.

    Until now the only way to issue a key was the admin CLI over a Cloud Run job, which meant
    an operator had to do it for every user.
    """
    if (body.team_id is None) == (body.user_id is None):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "one_principal_required",
                    "message": "Provide exactly one of team_id or user_id.",
                }
            },
        )
    try:
        minted = mint_api_key(
            session,
            tenant_id=principal.tenant_id,
            name=body.name,
            team_id=uuid.UUID(body.team_id) if body.team_id else None,
            user_id=uuid.UUID(body.user_id) if body.user_id else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"error": {"code": "invalid_principal", "message": str(exc)}}
        ) from exc
    return S.MintedKeyOut(
        id=str(minted.id), name=body.name, prefix=minted.prefix, secret=minted.secret
    )


@console.delete("/tenant/api-keys/{key_id}", status_code=204)
def revoke_key(key_id: str, session: Session = Depends(tenant_db_write)) -> None:
    """Revoke rather than delete: the usage ledger references the key, and losing the row would
    detach spend from whatever incurred it."""
    key = session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "not_found", "message": "No such key."}}
        )
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)


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
    session: Session = Depends(tenant_db_write),
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

    # Only configured when a project id is present, so local development and tests run without
    # Firebase at all — API keys keep working, which is the same asymmetry the design relies on
    # if Firebase is ever down.
    project = os.environ.get("BUGMINE_FIREBASE_PROJECT")
    app.state.firebase = TokenVerifier(project) if project else None
    app.include_router(public)
    app.include_router(catalog)
    app.include_router(check)
    app.include_router(ingest)
    app.include_router(console)

    # Served by the API rather than as a separate static host. One fewer deployable, and the
    # console shares an origin with the API so there is no CORS surface to get wrong.
    _console = Path(__file__).parent / "static" / "console.html"

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    @app.get("/console", include_in_schema=False, response_class=HTMLResponse)
    def console_page(response: Response) -> str:
        """Serve the app at the root, and at /console for links that already exist.

        One document serves both: signed out it is the landing page, signed in it is the
        console. Splitting them would mean two pages to keep in step, and the root 404'd
        entirely until now — a landing page nobody could reach without knowing an internal path.

        Injected rather than committed so the same image serves any environment, and read at
        request time so rotating the key does not need a rebuild. The key is not a secret —
        Identity Platform treats it as a project selector, and it is visible in every client
        that signs in — but it is environment-specific, which is reason enough not to hardcode
        it into the page.
        """
        # The console is a single unversioned document — HTML, CSS and JS in one file with no
        # build step and no content-hashed name. Without this, a browser keeps serving the copy
        # it already has and a deployed fix simply does not arrive: a fixed Sign out button
        # stayed invisible through three deploys because the page was never re-fetched, and
        # nothing distinguished that from the fix not working.
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return _console.read_text().replace(
            "__FIREBASE_BROWSER_KEY__", os.environ.get("BUGMINE_FIREBASE_BROWSER_KEY", "")
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
