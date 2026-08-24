"""Scheduled sweeping.

The interesting cases are all about *not* doing things: not re-enqueueing a source that isn't
due, not marking a source attempted when the queue rejected it, and not treating a source that
enqueues but never succeeds as healthy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from bugmine.db import tenant_session
from bugmine.models import PrivacyScope, Source, SubjectDomain
from bugmine.sweep import add_source, find_stale, record_failure, record_success, sweep
from sqlalchemy import Engine, select

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _url() -> str:
    return f"https://example.test/{uuid.uuid4().hex[:10]}"


class TestDueness:
    def test_a_never_run_source_is_due(self, engine: Engine) -> None:
        url = _url()
        with tenant_session(engine, None) as s:
            src = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            calls: list[dict] = []
            r = sweep(s, calls.append, now=NOW)
        # Asserted against this source rather than a global count: sources persist across tests
        # in a run, so "exactly one was due" is a claim about execution order, not about
        # dueness.
        assert url in [c["url"] for c in calls]
        assert r.enqueued >= 1
        assert src.last_attempt_at == NOW

    def test_a_recently_run_source_is_not_due(self, engine: Engine) -> None:
        url = _url()
        with tenant_session(engine, None) as s:
            src = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            src.last_attempt_at = NOW - timedelta(minutes=10)
            # Succeeded, not merely attempted. The two were indistinguishable in this fixture,
            # and the difference is what decides whether a retry is owed.
            src.last_success_at = NOW - timedelta(minutes=10)
            calls: list[dict] = []
            sweep(s, calls.append, now=NOW)
        assert url not in [c["url"] for c in calls], "a healthy source inside its interval ran"

    def test_a_source_whose_attempt_failed_retries_before_its_interval(
        self, engine: Engine
    ) -> None:
        """The sweep marks a source attempted when it enqueues, not when the fetch succeeds.

        115 sources rate-limited in one burst were all stamped attempted and went silent for a
        full day having fetched nothing — the catalog stopped growing and every source still
        looked healthy. A failed attempt must not satisfy the interval.
        """
        url = _url()
        with tenant_session(engine, None) as s:
            src = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            src.last_attempt_at = NOW - timedelta(minutes=15)
            src.last_success_at = None
            src.consecutive_failures = 1
            calls: list[dict] = []
            sweep(s, calls.append, now=NOW)
        assert url in [c["url"] for c in calls], "a failed source was not retried"

    def test_a_repeatedly_failing_source_backs_off(self, engine: Engine) -> None:
        """Doubling, so a genuinely broken source is not retried forever at full rate."""
        url = _url()
        with tenant_session(engine, None) as s:
            src = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            src.last_attempt_at = NOW - timedelta(minutes=15)
            src.last_success_at = None
            src.consecutive_failures = 5
            calls: list[dict] = []
            sweep(s, calls.append, now=NOW)
        assert url not in [c["url"] for c in calls], "a long-failing source retried too soon"

    def test_sweeping_twice_enqueues_once(self, engine: Engine) -> None:
        """A double-firing scheduler, or a manual run alongside cron, must not double-crawl."""
        with tenant_session(engine, None) as s:
            add_source(s, url=_url(), subject_domain=SubjectDomain.REPO_LIBRARY)
            calls: list[dict] = []
            sweep(s, calls.append, now=NOW)
            sweep(s, calls.append, now=NOW)
        assert len(calls) == 1

    def test_a_disabled_source_is_never_due(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            src = add_source(s, url=_url(), subject_domain=SubjectDomain.REPO_LIBRARY)
            src.enabled = False
            calls: list[dict] = []
            r = sweep(s, calls.append, now=NOW)
        assert r.enqueued == 0


class TestQueueFailures:
    def test_a_queue_failure_does_not_mark_the_source_attempted(self, engine: Engine) -> None:
        """Otherwise the source is skipped for a whole interval on work that never started."""

        def explode(_: dict) -> None:
            raise RuntimeError("queue unavailable")

        with tenant_session(engine, None) as s:
            src = add_source(s, url=_url(), subject_domain=SubjectDomain.REPO_LIBRARY)
            r = sweep(s, explode, now=NOW)
        assert r.enqueued == 0
        assert src.last_attempt_at is None, "a failed enqueue marked the source as attempted"


class TestStaleness:
    def test_a_source_that_enqueues_but_never_succeeds_is_stale(self, engine: Engine) -> None:
        """The failure mode error rates cannot see.

        The source is attempted on schedule and never succeeds. Nothing errors at the sweep
        level, the catalog keeps serving, and only absence of success reveals it.
        """
        with tenant_session(engine, None) as s:
            src = add_source(s, url=_url(), subject_domain=SubjectDomain.REPO_LIBRARY)
            src.last_attempt_at = NOW
            src.last_success_at = NOW - timedelta(days=5)  # interval is 1440 min
            stale = find_stale(s, now=NOW)
        assert src.url in [x.url for x in stale]

    def test_a_healthy_source_is_not_stale(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            src = add_source(s, url=_url(), subject_domain=SubjectDomain.REPO_LIBRARY)
            src.last_attempt_at = NOW
            src.last_success_at = NOW - timedelta(hours=2)
            stale = find_stale(s, now=NOW)
        assert src.url not in [x.url for x in stale]

    def test_success_clears_the_failure_counter(self, engine: Engine) -> None:
        url = _url()
        with tenant_session(engine, None) as s:
            src = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            record_failure(s, url)
            record_failure(s, url)
            assert src.consecutive_failures == 2
            record_success(s, url, now=NOW)
            assert src.consecutive_failures == 0
            assert src.last_success_at == NOW


class TestScope:
    def test_tenant_scope_requires_a_tenant(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s, pytest.raises(ValueError, match="tenant scope"):
            add_source(
                s,
                url=_url(),
                subject_domain=SubjectDomain.REPO_LIBRARY,
                privacy_scope=PrivacyScope.TENANT,
            )

    def test_a_tenant_source_is_invisible_to_others(
        self, engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        a, b = two_tenants
        url = _url()
        with tenant_session(engine, a) as s:
            add_source(
                s,
                url=url,
                subject_domain=SubjectDomain.REPO_LIBRARY,
                privacy_scope=PrivacyScope.TENANT,
                tenant_id=a,
            )
        with tenant_session(engine, b) as s:
            calls: list[dict] = []
            sweep(s, calls.append, now=NOW)
        assert all(c["url"] != url for c in calls), "tenant B swept tenant A's source"


class TestSuccessIsActuallyRecorded:
    """The sweep marks a source *attempted*; only the fetch can mark it succeeded.

    Nothing was calling record_success, so last_success_at stayed null for every source. Three
    things broke at once and all of them looked like something else: every source was
    permanently stale so the staleness alarm was meaningless, `failing` was permanently true so
    the retry backoff stayed pinned at its shortest interval, and the queue therefore refilled
    faster than it drained.
    """

    def test_the_crawl_worker_records_success(self) -> None:
        import inspect

        from bugmine.worker import app as worker

        source = inspect.getsource(worker.do_crawl)
        assert "record_success" in source, "a successful crawl must mark the source succeeded"

    def test_the_crawl_worker_records_failure(self) -> None:
        import inspect

        from bugmine.worker import app as worker

        source = inspect.getsource(worker.do_crawl)
        assert "record_failure" in source, "a failed crawl must count against the source"

    def test_recording_success_clears_the_failure_count(self, engine: Engine) -> None:
        """Otherwise a source that recovers keeps backing off as though it were still broken."""
        url = _url()
        with tenant_session(engine, None) as s:
            src = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            src.consecutive_failures = 4
            record_failure(s, url)
            record_success(s, url)
        with tenant_session(engine, None, commit=False) as s:
            found = s.execute(select(Source).where(Source.url == url)).scalars().first()
        assert found.consecutive_failures == 0
        assert found.last_success_at is not None


class TestAddSourceIsIdempotent:
    def test_registering_the_same_url_twice_yields_one_source(self, engine: Engine) -> None:
        """Re-running a source list duplicated every entry in it — twice. A source registered
        twice is crawled twice, spending the rate-limit budget that decides how much of the
        catalog refreshes in an hour."""
        url = _url()
        with tenant_session(engine, None) as s:
            first = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            second = add_source(s, url=url, subject_domain=SubjectDomain.REPO_LIBRARY)
            assert first.id == second.id
        with tenant_session(engine, None, commit=False) as s:
            rows = s.execute(select(Source).where(Source.url == url)).scalars().all()
        assert len(rows) == 1


class TestEcosystemMustAgreeWithTheDomain:
    """`check` and `scan` derive the domain from the caller's ecosystem and retrieve on the
    pair, so a source registered with a contradictory pair produces records that are never
    reachable — and the failure surfaces as "nothing known about this component"."""

    def test_a_language_runtime_under_a_package_ecosystem_is_refused(self, engine: Engine) -> None:
        """The live catalog has `kotlin` and `elixir` registered exactly this way, which is
        986 records that no scan can retrieve."""
        with tenant_session(engine, None) as s:
            with pytest.raises(ValueError, match="never retrieved"):
                add_source(
                    s,
                    url=_url(),
                    subject_domain=SubjectDomain.LANGUAGE_RUNTIME,
                    component_ref="kotlin",
                    ecosystem="pypi",
                )

    def test_an_agreeing_pair_is_accepted(self, engine: Engine) -> None:
        with tenant_session(engine, None) as s:
            src = add_source(
                s,
                url=_url(),
                subject_domain=SubjectDomain.REPO_LIBRARY,
                component_ref="transformers",
                ecosystem="pypi",
            )
            assert src.ecosystem == "pypi"

    def test_no_ecosystem_is_accepted(self, engine: Engine) -> None:
        """Domains that ship no package registry — SaaS platforms, LLM models — have none."""
        with tenant_session(engine, None) as s:
            src = add_source(s, url=_url(), subject_domain=SubjectDomain.LLM_MODEL)
            assert src.ecosystem is None

    def test_an_ecosystem_the_check_path_does_not_know_is_left_alone(self, engine: Engine) -> None:
        """Refusing these would block registering a source before the ecosystem is supported,
        which is the wrong end to fix it from."""
        with tenant_session(engine, None) as s:
            src = add_source(
                s,
                url=_url(),
                subject_domain=SubjectDomain.LANGUAGE_RUNTIME,
                component_ref="kotlin",
                ecosystem="maven-central-snapshot",
            )
            assert src.ecosystem == "maven-central-snapshot"
