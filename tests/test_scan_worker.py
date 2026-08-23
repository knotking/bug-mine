"""The scan workers, end to end over a real snapshot.

The split between them is a security boundary, not an organisational one: fetch has egress and
no model, analyse has a model and no egress. A repository is content a tenant chose, so a model
reachable from a process that can also make outbound requests is what turns prompt injection
into exfiltration (ADR-0005, NFR-42).
"""

from __future__ import annotations

import io
import tarfile
import uuid
from pathlib import Path

import pytest
from bugmine.catalog import IncomingBug, write
from bugmine.db import tenant_session
from bugmine.models import (
    BugType,
    Finding,
    Job,
    JobState,
    RecordOrigin,
    Scan,
    SubjectDomain,
)
from bugmine.worker import snapshot
from sqlalchemy import select
from sqlalchemy.engine import Engine

USES_SYMBOL = "from scanlib import eval_type_backport\n\neval_type_backport()\n"
IGNORES_SYMBOL = "from scanlib import BaseModel\n\n\nclass M(BaseModel):\n    pass\n"
LOCKFILE = "scanlib==2.5.0\n"


def _seed_catalog(engine: Engine, title: str) -> None:
    with tenant_session(engine, None) as s:
        write(
            s,
            IncomingBug(
                subject_domain=SubjectDomain.REPO_LIBRARY,
                component_ref="scanlib",
                ecosystem="pypi",
                bug_type=BugType.BREAKING_CHANGE,
                applicability={
                    "kind": "version_range",
                    "scheme": "generic",
                    "introduced_in": "2.0.0",
                    "fixed_in": None,
                },
                title=title,
                evidence_url="https://example.invalid/rel",
            ),
            origin=RecordOrigin.CRAWLED,
        )


def _snapshot_bytes(files: dict[str, str], tmp_path: Path) -> bytes:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(body)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(root)))
    return buffer.getvalue()


class TestSourceCollection:
    def test_vendored_dependencies_are_skipped(self, tmp_path: Path) -> None:
        """Reachability asks whether *this project* touches the symbol. Walking into installed
        packages answers a different and far larger question — stage two, not this."""
        (tmp_path / "app.py").write_text(USES_SYMBOL)
        vendored = tmp_path / ".venv" / "lib" / "site-packages" / "pydantic"
        vendored.mkdir(parents=True)
        (vendored / "main.py").write_text(USES_SYMBOL)

        collected = snapshot.collect_sources(tmp_path)
        assert "app.py" in collected
        assert not any("site-packages" in p for p in collected)

    def test_a_huge_file_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "generated.py").write_text("x = 1\n" * 400_000)
        (tmp_path / "app.py").write_text(USES_SYMBOL)
        collected = snapshot.collect_sources(tmp_path)
        assert "app.py" in collected
        assert "generated.py" not in collected


class TestCloneGuard:
    def test_a_private_address_is_refused_before_git_runs(self) -> None:
        """The scan target is chosen by a tenant, and this worker holds egress so it can reach
        the internet. Without the guard it is an SSRF proxy into the VPC."""
        with pytest.raises(snapshot.SnapshotError) as exc:
            snapshot.clone("http://169.254.169.254/latest/meta-data/", Path("/tmp/nope"))
        assert exc.value.code != "clone_failed", "rejected by the guard, not by git"


class TestAnalyseWorker:
    def _run(  # type: ignore[no-untyped-def]
        self,
        worker_client,
        engine: Engine,
        tenant: uuid.UUID,
        files: dict[str, str],
        tmp_path: Path,
    ):
        with tenant_session(engine, tenant) as s:
            job = Job(tenant_id=tenant, job_type="scan_analyze", payload={})
            s.add(job)
            s.flush()
            scan = Scan(job_id=job.id, tenant_id=tenant, repo_ref="https://example.invalid/r.git")
            s.add(scan)
            s.flush()
            scan_id = scan.id

        worker_client.app.state.storage.bucket("snap").blob("s.tar.gz").upload_from_string(
            _snapshot_bytes(files, tmp_path)
        )
        response = worker_client.post(
            "/work/scan/analyze",
            json={
                "snapshot_uri": "gs://snap/s.tar.gz",
                "tenant_id": str(tenant),
                "scan_id": str(scan_id),
                "commit_sha": "abc123",
            },
        )
        return response, scan_id

    def test_an_unreachable_defect_is_suppressed_not_reported(
        self, worker_client, engine: Engine, two_tenants, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """The product claim, end to end: the dependency is affected, the project is not."""
        tenant, _ = two_tenants
        _seed_catalog(engine, "Remove support for `eval_type_backport()`")
        response, _ = self._run(
            worker_client, engine, tenant,
            {"requirements.txt": LOCKFILE, "app.py": IGNORES_SYMBOL}, tmp_path,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["findings"] == 0
        assert body["suppressed_unreachable"] == 1

    def test_a_reachable_defect_is_written_with_its_citation(
        self, worker_client, engine: Engine, two_tenants, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        tenant, _ = two_tenants
        _seed_catalog(engine, "Remove support for `eval_type_backport()`")
        response, scan_id = self._run(
            worker_client, engine, tenant,
            {"requirements.txt": LOCKFILE, "app.py": USES_SYMBOL}, tmp_path,
        )
        assert response.json()["findings"] == 1
        with tenant_session(engine, tenant, commit=False) as s:
            findings = s.execute(select(Finding).where(Finding.scan_id == scan_id)).scalars().all()
            assert len(findings) == 1
            assert findings[0].reachable is True
            # Read inside the session: citations lazy-load, and asserting on them after it
            # closes tests SQLAlchemy's detachment rather than the finding.
            assert findings[0].citations, "a finding without a citation is ungrounded"

    def test_the_job_is_closed_out_under_its_tenant(
        self, worker_client, engine: Engine, two_tenants, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """A tenant-scoped job finished from an anonymous session finds nothing and returns
        quietly, leaving the job RUNNING forever — indistinguishable from a hung worker."""
        tenant, _ = two_tenants
        _seed_catalog(engine, "Remove support for `eval_type_backport()`")
        response, _ = self._run(
            worker_client, engine, tenant,
            {"requirements.txt": LOCKFILE, "app.py": USES_SYMBOL}, tmp_path,
        )
        job_id = uuid.UUID(response.json()["job_id"])
        with tenant_session(engine, tenant, commit=False) as s:
            job = s.get(Job, job_id)
        assert job is not None and job.state is JobState.SUCCEEDED

    def test_a_repo_with_no_lockfile_says_so_rather_than_reporting_clean(
        self, worker_client, engine: Engine, two_tenants, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """An unpinned pyproject yields no versions, deliberately — a range cannot be matched
        against applicability. But zero findings over unread dependencies looks exactly like
        zero findings over a clean repository, and FR-39 forbids presenting the second as the
        first. This is the real shape: httpx declares dependencies and ships no lockfile.
        """
        tenant, _ = two_tenants
        response, scan_id = self._run(
            worker_client, engine, tenant,
            {"pyproject.toml": '[project]\nname = "app"\ndependencies = ["scanlib>=2"]\n',
             "app.py": USES_SYMBOL},
            tmp_path,
        )
        body = response.json()
        assert body["findings"] == 0
        assert body["unresolved_manifests"], "an unreadable manifest must be reported"
        with tenant_session(engine, tenant, commit=False) as s:
            assert s.get(Scan, scan_id).unresolved_manifests >= 1
