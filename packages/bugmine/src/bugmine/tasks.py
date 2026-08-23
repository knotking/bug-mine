"""Cloud Tasks enqueue.

Cloud Tasks over Pub/Sub because per-queue dispatch rate limiting is the noisy-neighbour
control: a tenant merging a dependency bump across forty services generates forty scans in a
minute, which is within their token quota but would monopolise a shared pipe.

Tasks deliver HTTP requests, so each queue targets a worker service directly. `ingestion.md`
models workers as Cloud Run *jobs* behind a dispatcher — necessary when work can exceed a
request timeout, which crawl and extract cannot.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from google.cloud import tasks_v2


@dataclass(frozen=True)
class QueueTarget:
    queue: str
    url: str
    service_account: str


def _client() -> tasks_v2.CloudTasksClient:
    return tasks_v2.CloudTasksClient()


def enqueue(
    target: QueueTarget,
    payload: dict[str, Any],
    *,
    project: str | None = None,
    location: str | None = None,
    client: tasks_v2.CloudTasksClient | None = None,
) -> str:
    """Put one unit of work on a queue.

    The task carries an OIDC token for the worker's service account, so the worker can stay
    `INGRESS_TRAFFIC_INTERNAL_ONLY` and still refuse anything that is not Cloud Tasks. Making
    the workers publicly invokable to simplify this would put an unauthenticated endpoint in
    front of the component that fetches attacker-supplied URLs.
    """
    project = project or os.environ["GOOGLE_CLOUD_PROJECT"]
    location = location or os.environ.get("BUGMINE_REGION", "us-central1")
    client = client or _client()

    parent = client.queue_path(project, location, target.queue)
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": target.url,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode(),
            "oidc_token": {
                "service_account_email": target.service_account,
                "audience": target.url.rsplit("/work", 1)[0],
            },
        }
    }
    created = client.create_task(parent=parent, task=task)
    return created.name


def crawl_target() -> QueueTarget:
    return QueueTarget(
        queue="bugmine-crawl",
        url=os.environ["BUGMINE_CRAWL_URL"].rstrip("/") + "/work/crawl",
        service_account=os.environ["BUGMINE_TASK_INVOKER_SA"],
    )


def extract_target() -> QueueTarget:
    return QueueTarget(
        queue="bugmine-extract",
        url=os.environ["BUGMINE_EXTRACT_URL"].rstrip("/") + "/work/extract",
        service_account=os.environ["BUGMINE_TASK_INVOKER_SA"],
    )


def scan_fetch_target() -> QueueTarget:
    return QueueTarget(
        queue="bugmine-scan-fetch",
        url=os.environ["BUGMINE_SCAN_FETCH_URL"].rstrip("/") + "/work/scan/fetch",
        service_account=os.environ["BUGMINE_TASK_INVOKER_SA"],
    )


def scan_analyze_target() -> QueueTarget:
    return QueueTarget(
        queue="bugmine-scan-analyze",
        url=os.environ["BUGMINE_SCAN_ANALYZE_URL"].rstrip("/") + "/work/scan/analyze",
        service_account=os.environ["BUGMINE_TASK_INVOKER_SA"],
    )
