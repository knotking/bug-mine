"""Worker HTTP surface, invoked by Cloud Tasks.

A deviation from the architecture worth naming: `ingestion.md` models workers as Cloud Run
*jobs* behind a dispatcher, because Cloud Tasks cannot start a job execution directly. Crawl
and extract are short-running, so a service invoked by Tasks does the same work with one fewer
component. The dispatcher becomes necessary when a worker can exceed a request timeout — the
scan workers, which are not built yet.

The egress split is preserved and it is the part that matters: crawl and extract are separate
services with separate identities, and only extract can reach a model.
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from google import genai
from google.cloud import storage
from pydantic import BaseModel
from sqlalchemy import select

from bugmine import github_app, metering
from bugmine import scan as scan_mod
from bugmine.catalog import write
from bugmine.db import make_engine, tenant_session
from bugmine.inventory import collect
from bugmine.models import (
    Component,
    CostBearer,
    JobState,
    JobType,
    PrivacyScope,
    RecordOrigin,
    Scan,
    SubjectDomain,
    UsagePurpose,
)
from bugmine.models.jobs import Job
from bugmine.worker import crawl as crawl_mod
from bugmine.worker import extract as extract_mod
from bugmine.worker import osv as osv_mod
from bugmine.worker import snapshot, structured

logger = logging.getLogger("bugmine.worker")




router = APIRouter(prefix="/work", tags=["worker"])


class CrawlRequest(BaseModel):
    url: str
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY
    component_ref: str | None = None
    ecosystem: str | None = None


class ScanFetchRequest(BaseModel):
    repo_url: str
    tenant_id: str
    ref: str | None = None
    scan_id: str | None = None


class ScanAnalyzeRequest(BaseModel):
    snapshot_uri: str
    tenant_id: str
    scan_id: str
    commit_sha: str | None = None


class OsvRequest(BaseModel):
    ecosystem: str
    name: str


class ExtractRequest(BaseModel):
    artifact_uri: str
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY
    component_ref: str | None = None
    ecosystem: str | None = None
    mode: str = "auto"
    """`auto` uses the deterministic parser for feeds whose structure is known and the model
    for everything else. `structured` or `llm` force one."""


@router.post("/scan/fetch")
def do_scan_fetch(body: ScanFetchRequest, request: Request) -> dict[str, Any]:
    """Clone a repository into a snapshot, then hand off to analysis.

    This worker has egress and no model; the analyser has a model and no egress. The split is
    the security boundary (ADR-0005, NFR-42), not an organisational one: a repository is
    content chosen by a tenant, and a model reachable from a process that can also make
    outbound requests is what prompt injection needs to become exfiltration.
    """
    app = request.app
    tenant_id = uuid.UUID(body.tenant_id)
    job_id = _record_job(
        app, JobType.SCAN_FETCH, body.model_dump(mode="json"), tenant_id=tenant_id
    )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commit_sha = snapshot.clone(body.repo_url, root, ref=body.ref)
            stored = snapshot.store(
                root,
                bucket_name=app.state.snapshot_bucket,
                client=app.state.storage,
                tenant_id=tenant_id,
            )
    except snapshot.SnapshotError as exc:
        logger.exception("scan fetch failed for %s", body.repo_url)
        _finish_job(
            app, job_id, JobState.FAILED, f"{exc.code}: {exc}"[:500], tenant_id=tenant_id
        )
        raise HTTPException(status_code=422, detail={"error": {"code": exc.code}}) from exc
    except Exception as exc:
        logger.exception("scan fetch failed for %s", body.repo_url)
        _finish_job(
            app,
            job_id,
            JobState.FAILED,
            f"{type(exc).__name__}: {exc}"[:500],
            tenant_id=tenant_id,
        )
        raise HTTPException(status_code=500, detail={"error": {"code": "fetch_failed"}}) from exc

    scan_id = body.scan_id
    with tenant_session(app.state.engine, tenant_id) as s:
        if scan_id is None:
            scan = Scan(job_id=job_id, tenant_id=tenant_id, repo_ref=body.repo_url)
            s.add(scan)
            s.flush()
            scan_id = str(scan.id)
        else:
            existing = s.get(Scan, uuid.UUID(scan_id))
            if existing is not None:
                existing.commit_sha = commit_sha

    if os.environ.get("BUGMINE_SCAN_ANALYZE_URL"):
        from bugmine import tasks

        tasks.enqueue(
            tasks.scan_analyze_target(),
            {
                "snapshot_uri": stored.uri,
                "tenant_id": str(tenant_id),
                "scan_id": scan_id,
                "commit_sha": commit_sha,
            },
        )

    _finish_job(app, job_id, JobState.SUCCEEDED, tenant_id=tenant_id)
    return {
        "job_id": str(job_id),
        "scan_id": scan_id,
        "snapshot_uri": stored.uri,
        "commit_sha": commit_sha,
        "files": stored.file_count,
        "bytes": stored.bytes_stored,
    }


@router.post("/scan/analyze")
def do_scan_analyze(body: ScanAnalyzeRequest, request: Request) -> dict[str, Any]:
    """Inventory the snapshot, match the catalog, narrow by reachability, write findings.

    No egress beyond the model endpoint. Everything this reads came from the fetch worker, and
    everything it writes is grounded in a catalog record.
    """
    app = request.app
    tenant_id = uuid.UUID(body.tenant_id)
    scan_id = uuid.UUID(body.scan_id)
    job_id = _record_job(
        app, JobType.SCAN_ANALYZE, body.model_dump(mode="json"), tenant_id=tenant_id
    )

    try:
        blob_bytes = _read_snapshot(app, body.snapshot_uri)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(fileobj=io.BytesIO(blob_bytes), mode="r:gz") as archive:
                # filter="data" refuses absolute paths and traversal members. The archive was
                # written by our own fetch worker, but it was built from a tenant's repository
                # and a file named ../../etc/passwd is a thing a repository can contain.
                archive.extractall(root, filter="data")

            inventory = collect(root)
            sources = snapshot.collect_sources(root)

        with tenant_session(app.state.engine, tenant_id) as s:
            outcome = scan_mod.analyse(
                s,
                scan_id=scan_id,
                dependencies=list(inventory.dependencies),
                sources=sources,
            )
            for finding in outcome.findings:
                s.add(finding)

            record = s.get(Scan, scan_id)
            if record is not None:
                record.commit_sha = body.commit_sha
                record.uncovered_components = len(outcome.uncovered)
                record.unresolved_manifests = len(inventory.unresolved)
                record.suppressed_unreachable = outcome.suppressed
                record.dependencies_scanned = len(inventory.dependencies)
                # Every finding is built from a match, so grounding cannot be partial here.
                record.grounding_complete = True
    except Exception as exc:
        logger.exception("scan analysis failed for %s", body.snapshot_uri)
        _finish_job(
            app,
            job_id,
            JobState.FAILED,
            f"{type(exc).__name__}: {exc}"[:500],
            tenant_id=tenant_id,
        )
        raise HTTPException(status_code=500, detail={"error": {"code": "analysis_failed"}}) from exc

    _publish_check_run(app, tenant_id, scan_id, outcome, inventory)

    _finish_job(app, job_id, JobState.SUCCEEDED, tenant_id=tenant_id)
    return {
        "job_id": str(job_id),
        "scan_id": str(scan_id),
        "dependencies": len(inventory.dependencies),
        "sources_analysed": len(sources),
        "findings": len(outcome.findings),
        # Surfaced rather than hidden: a scan that suppressed everything is indistinguishable
        # from one that found nothing, and the difference is the entire product claim.
        "suppressed_unreachable": outcome.suppressed,
        "uncovered_components": len(outcome.uncovered),
        "unresolved_manifests": list(inventory.unresolved),
        "unanalysed_ecosystems": sorted(outcome.unanalysed_ecosystems),
    }


def _publish_check_run(app: Any, tenant_id, scan_id, outcome, inventory) -> None:  # type: ignore[no-untyped-def]
    """Report back to GitHub when this scan came from a pull request.

    Failures here are logged and swallowed. The findings are already written and readable
    through the API; losing the Check Run is a degraded report, and raising would fail the whole
    analysis and lose the scan itself over a reporting step.
    """
    app_id = os.environ.get("BUGMINE_GITHUB_APP_ID", "")
    key = os.environ.get("BUGMINE_GITHUB_PRIVATE_KEY", "")
    if not (app_id and key):
        return

    with tenant_session(app.state.engine, tenant_id, commit=False) as s:
        scan = s.get(Scan, scan_id)
        job = s.get(Job, scan.job_id) if scan else None
        payload = (job.payload or {}) if job else {}
        repo_full_name = payload.get("repo_full_name") or (scan.repo_ref if scan else "")
        head_sha = payload.get("ref") or (scan.commit_sha if scan else "")
        installation = payload.get("installation_id")

    if not (installation and payload.get("pr")):
        return  # Not a pull request scan; nothing to report to.

    summary = github_app.CheckRunSummary(
        findings=len(outcome.findings),
        suppressed=outcome.suppressed,
        uncovered=len(outcome.uncovered),
        unresolved=len(inventory.unresolved),
    )
    with tenant_session(app.state.engine, tenant_id, commit=False) as s:
        components = {c.id: c.canonical_ref for c in s.execute(select(Component)).scalars()}
    rows = [
        {
            "component": components.get(f.component_id, "unknown"),
            "title": f.title,
            "reachable": f.reachable,
            "detail": f.detail,
            "citations": [str(c.bug_version_id) for c in f.citations],
        }
        for f in outcome.findings
    ]
    try:
        token = github_app.installation_token(app_id, key, int(installation))
        github_app.post_check_run(
            token=token,
            repo_full_name=repo_full_name,
            head_sha=head_sha,
            summary=summary,
            findings=rows,
        )
    except Exception:
        logger.exception("could not publish check run for scan %s", scan_id)


def _read_snapshot(app: Any, uri: str) -> bytes:
    bucket_name, _, blob_name = uri.removeprefix("gs://").partition("/")
    return app.state.storage.bucket(bucket_name).blob(blob_name).download_as_bytes()


def _record_job(
    app: Any,
    job_type: JobType,
    payload: dict[str, Any],
    *,
    tenant_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Record a job, under its tenant when it has one.

    The session has to carry the tenant, not just the row: RLS checks the write against the
    session's tenant context, so inserting a tenant-scoped job from an anonymous session is
    refused outright.
    """
    with tenant_session(app.state.engine, tenant_id) as s:
        job = Job(job_type=job_type, state=JobState.RUNNING, payload=payload, tenant_id=tenant_id)
        s.add(job)
        s.flush()
        return job.id


def _finish_job(
    app: FastAPI,
    job_id: uuid.UUID,
    state: JobState,
    reason: str | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
) -> None:
    """Close out a job. The tenant must match the one it was opened under.

    An anonymous session sees only tenant-less rows, so finishing a tenant-scoped job from one
    finds nothing and returns quietly — leaving the job RUNNING forever with no error to say
    why, which is indistinguishable from a worker that hung.
    """
    with tenant_session(app.state.engine, tenant_id) as s:
        job = s.get(Job, job_id)
        if job is not None:
            job.state = state
            job.failure_reason = reason


@router.post("/osv")
def do_osv(body: OsvRequest, request: Request) -> dict[str, Any]:
    """Ingest OSV advisories for one package.

    Fetch and write in one worker, unlike crawl and extract. That split exists because
    extraction feeds untrusted prose to a model; OSV goes nowhere near one — the ranges are
    declared, so a parser is both cheaper and more certain, and there is no injection surface to
    contain.
    """
    app = request.app
    job_id = _record_job(app, JobType.CRAWL, body.model_dump(mode="json"))
    try:
        payload = osv_mod.fetch(body.ecosystem, body.name)
        result = osv_mod.parse(payload)

        written = deduped = 0
        with tenant_session(app.state.engine, None) as s:
            for bug in result.bugs:
                outcome = write(
                    s, bug, origin=RecordOrigin.CRAWLED, privacy_scope=PrivacyScope.PUBLIC
                )
                if outcome.created_version:
                    written += 1
                else:
                    deduped += 1
    except Exception as exc:
        logger.exception("osv ingest failed for %s/%s", body.ecosystem, body.name)
        _finish_job(app, job_id, JobState.FAILED, f"{type(exc).__name__}: {exc}"[:500])
        raise HTTPException(status_code=502, detail={"error": {"code": "osv_failed"}}) from exc

    _finish_job(app, job_id, JobState.SUCCEEDED)
    return {
        "job_id": str(job_id),
        "advisories": len(payload.get("vulns") or []),
        "records": len(result.bugs),
        "written": written,
        "deduped": deduped,
        # Surfaced rather than dropped: an advisory the source has retracted must not quietly
        # become a finding, and the count is how we notice if that starts happening often.
        "withdrawn": result.withdrawn,
        "skipped": result.skipped,
    }


@router.post("/crawl")
def do_crawl(body: CrawlRequest, request: Request) -> dict[str, Any]:
    app = request.app
    job_id = _record_job(app, JobType.CRAWL, body.model_dump(mode="json"))
    try:
        result = crawl_mod.fetch_and_store(
            body.url, bucket_name=app.state.artifact_bucket, client=app.state.storage
        )
    except Exception as exc:
        logger.exception("crawl failed for %s", body.url)
        _finish_job(app, job_id, JobState.FAILED, f"{type(exc).__name__}: {exc}"[:500])
        raise HTTPException(status_code=502, detail={"error": {"code": "fetch_failed"}}) from exc

    # Chain extraction only when the fetch produced something new. This is where the dedup
    # gate turns into money saved: an unchanged source enqueues nothing and spends no tokens.
    chained = False
    if result.changed and os.environ.get("BUGMINE_EXTRACT_URL"):
        from bugmine import tasks

        tasks.enqueue(
            tasks.extract_target(),
            {
                "artifact_uri": result.uri,
                "subject_domain": body.subject_domain.value,
                "component_ref": body.component_ref,
                "ecosystem": body.ecosystem,
            },
        )
        chained = True

    _finish_job(app, job_id, JobState.SUCCEEDED)
    return {
        "job_id": str(job_id),
        "chained_extraction": chained,
        "artifact_uri": result.uri,
        "content_hash": result.content_hash,
        "bytes": result.bytes_fetched,
        # The dedup gate. False here means extraction should not run.
        "changed": result.changed,
    }


@router.post("/extract")
def do_extract(body: ExtractRequest, request: Request) -> dict[str, Any]:
    app = request.app
    job_id = _record_job(app, JobType.EXTRACT, body.model_dump(mode="json"))
    try:
        document = extract_mod.read_artifact(body.artifact_uri, client=app.state.storage)

        # A GitHub releases feed states its versions rather than implying them, so a parser is
        # both cheaper and more accurate than a model. The model is for prose changelogs where
        # the defect has to be read out of a sentence.
        use_structured = body.mode == "structured" or (
            body.mode == "auto" and structured.looks_like_github_releases(document)
        )

        if use_structured:
            parsed = structured.extract_github_releases(
                document,
                component_ref=body.component_ref or "unknown",
                subject_domain=body.subject_domain,
                ecosystem=body.ecosystem,
                artifact_uri=body.artifact_uri,
            )
            bugs, rejected, engine = parsed.bugs, 0, "structured"
            usage = metering.Spend()  # the parser spends nothing, and must not appear to
        else:
            result = extract_mod.extract(
                document,
                genai_client=app.state.genai,
                model=app.state.model,
                subject_domain=body.subject_domain,
                ecosystem=body.ecosystem,
                artifact_uri=body.artifact_uri,
                default_component=body.component_ref,
            )
            bugs, rejected, engine = result.bugs, result.rejected, "llm"
            usage = result.usage

        written = 0
        deduped = 0
        with tenant_session(app.state.engine, None) as s:
            if usage.total:
                # Written here rather than inside extract(): only the caller knows who bears
                # the cost, and reading the bearer from the worker would misattribute every
                # tenant-triggered crawl to the system.
                job = s.get(Job, job_id)
                metering.record(
                    s,
                    job=job,
                    spend=usage,
                    model_id=app.state.model,
                    purpose=UsagePurpose.EXTRACT,
                    # Crawling to build the shared catalog is system-borne. A tenant-triggered
                    # ingestion would pass its tenant here; that path does not exist yet.
                    cost_bearer=CostBearer.SYSTEM,
                )
            for bug in bugs:
                # Scope is decided here, by the job's origin — never by the extractor. Letting
                # model output choose visibility would put a privacy decision in the code path
                # most exposed to injected content.
                outcome = write(
                    s, bug, origin=RecordOrigin.CRAWLED, privacy_scope=PrivacyScope.PUBLIC
                )
                if outcome.created_version:
                    written += 1
                else:
                    deduped += 1
    except Exception as exc:
        logger.exception("extraction failed for %s", body.artifact_uri)
        _finish_job(app, job_id, JobState.FAILED, f"{type(exc).__name__}: {exc}"[:500])
        raise HTTPException(
            status_code=500, detail={"error": {"code": "extraction_failed"}}
        ) from exc

    _finish_job(app, job_id, JobState.SUCCEEDED)
    return {
        "job_id": str(job_id),
        "engine": engine,
        "extracted": len(bugs),
        "written": written,
        "deduplicated": deduped,
        "rejected_by_validation": rejected,
    }


def create_worker_app(engine: Any = None) -> FastAPI:
    app = FastAPI(title="BugMine Worker", version="1.0.0")
    app.state.engine = engine or make_engine()
    app.state.artifact_bucket = os.environ.get("BUGMINE_ARTIFACT_BUCKET", "")
    app.state.snapshot_bucket = os.environ.get("BUGMINE_SNAPSHOT_BUCKET", "")
    app.state.model = os.environ.get("BUGMINE_MODEL", "gemini-3.7-flash")
    app.state.storage = storage.Client()

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("BUGMINE_VERTEX_LOCATION", "us-central1")
    app.state.genai = genai.Client(vertexai=True, project=project, location=location)

    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
