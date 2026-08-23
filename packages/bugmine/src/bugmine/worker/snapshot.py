"""Fetch a repository into a short-lived snapshot.

Runs in the worker that **has egress and no model**. It reaches an arbitrary URL supplied by a
tenant, so it is the half of scanning that must never see a model: a repository is untrusted
content, and a prompt injection reaching a model from a process that can also make outbound
requests is the failure ADR-0005 exists to prevent.

Snapshots live in a bucket with a 24-hour lifecycle rule. Deletion is a bucket policy rather
than application code because NFR-20 is a promise about data at rest — a worker that crashes
before its cleanup step must not be able to leave source behind.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from bugmine import urlguard

MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
"""A repository larger than this is refused rather than streamed. The limit exists so one
pathological input cannot exhaust the worker's disk and take down every other scan with it."""

MAX_FILE_BYTES = 2 * 1024 * 1024
"""Per-file cap for source collected into the analysis payload. A generated file megabytes long
contributes nothing to reachability and would dominate the transfer."""

CLONE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class SnapshotResult:
    uri: str
    commit_sha: str | None
    bytes_stored: int
    file_count: int


class SnapshotError(Exception):
    """Raised with a stable code. Callers branch on the code, never the message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            check=True,
            # Git will otherwise prompt for credentials on a private URL and hang until the
            # timeout, turning an authorisation failure into a five-minute stall.
            env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError("clone_timeout", "The repository took too long to fetch.") from exc
    except subprocess.CalledProcessError as exc:
        raise SnapshotError("clone_failed", (exc.stderr or "git failed").strip()[:300]) from exc
    return completed.stdout.strip()


def clone(repo_url: str, destination: Path, *, ref: str | None = None) -> str | None:
    """Shallow-clone a repository, returning the commit SHA.

    The URL is validated first, by the same guard the crawler uses. A scan target is chosen by
    a tenant, so without this the worker is an SSRF proxy into the VPC — and it holds egress
    precisely so it can reach the internet.
    """
    if rejection := urlguard.check(repo_url):
        raise SnapshotError(rejection.code, rejection.message)

    args = ["clone", "--depth", "1", "--no-tags", "--single-branch"]
    if ref:
        args += ["--branch", ref]
    _run_git([*args, repo_url, str(destination)])
    return _run_git(["rev-parse", "HEAD"], cwd=destination) or None


def collect_sources(root: Path, *, suffixes: tuple[str, ...] = (".py",)) -> dict[str, str]:
    """Read the project's own source for reachability analysis.

    Vendored dependency trees are skipped: reachability asks whether *this project* references
    the affected symbol, and walking into installed packages answers a different, much larger
    question — stage two of ADR-0006 rather than this.
    """
    skip = {
        ".git", "node_modules", "site-packages", "dist-packages", ".venv", "venv",
        "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "build", "dist",
    }
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if skip & set(path.relative_to(root).parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            out[str(path.relative_to(root))] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # An unreadable file is a gap in the analysis, and reachability already treats a
            # gap as undetermined rather than as absence.
            continue
    return out


def store(root: Path, *, bucket_name: str, client, tenant_id: uuid.UUID) -> SnapshotResult:  # type: ignore[no-untyped-def]
    """Archive the working tree into the snapshot bucket.

    Keyed by tenant so a lifecycle rule, an audit, or a deletion request can act on one
    tenant's snapshots without touching another's.
    """
    buffer = io.BytesIO()
    file_count = 0
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".git" in path.relative_to(root).parts:
                continue
            archive.add(path, arcname=str(path.relative_to(root)))
            file_count += 1
            if buffer.tell() > MAX_SNAPSHOT_BYTES:
                raise SnapshotError("snapshot_too_large", "The repository exceeds the size limit.")

    key = f"{tenant_id}/{uuid.uuid4()}.tar.gz"
    blob = client.bucket(bucket_name).blob(key)
    blob.upload_from_string(buffer.getvalue())
    return SnapshotResult(
        uri=f"gs://{bucket_name}/{key}",
        commit_sha=None,
        bytes_stored=buffer.tell(),
        file_count=file_count,
    )
