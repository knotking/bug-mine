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

import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from google import genai
from google.cloud import storage
from pydantic import BaseModel

from bugmine.catalog import write
from bugmine.db import make_engine, tenant_session
from bugmine.models import JobState, JobType, PrivacyScope, RecordOrigin, SubjectDomain
from bugmine.models.jobs import Job
from bugmine.worker import crawl as crawl_mod
from bugmine.worker import extract as extract_mod
from bugmine.worker import structured

logger = logging.getLogger("bugmine.worker")

router = APIRouter(prefix="/work", tags=["worker"])


class CrawlRequest(BaseModel):
    url: str
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY
    component_ref: str | None = None
    ecosystem: str | None = None


class ExtractRequest(BaseModel):
    artifact_uri: str
    subject_domain: SubjectDomain = SubjectDomain.REPO_LIBRARY
    component_ref: str | None = None
    ecosystem: str | None = None
    mode: str = "auto"
    """`auto` uses the deterministic parser for feeds whose structure is known and the model
    for everything else. `structured` or `llm` force one."""


def _record_job(app: Any, job_type: JobType, payload: dict[str, Any]) -> uuid.UUID:
    with tenant_session(app.state.engine, None) as s:
        job = Job(job_type=job_type, state=JobState.RUNNING, payload=payload)
        s.add(job)
        s.flush()
        return job.id


def _finish_job(
    app: FastAPI, job_id: uuid.UUID, state: JobState, reason: str | None = None
) -> None:
    with tenant_session(app.state.engine, None) as s:
        job = s.get(Job, job_id)
        if job is not None:
            job.state = state
            job.failure_reason = reason


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

        written = 0
        deduped = 0
        with tenant_session(app.state.engine, None) as s:
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
    app.state.model = os.environ.get("BUGMINE_MODEL", "gemini-2.0-flash")
    app.state.storage = storage.Client()

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("BUGMINE_VERTEX_LOCATION", "us-central1")
    app.state.genai = genai.Client(vertexai=True, project=project, location=location)

    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
