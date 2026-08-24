"""The GitHub App: pull request to Check Run.

Two things carry real risk here. A webhook is untrusted input that can start a scan against any
repository we can reach, so the signature is checked before the body is parsed. And a Check Run
that says nothing reads as approval, so silence is never allowed to stand in for coverage.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from bugmine.github_app import (
    CheckRunSummary,
    WebhookError,
    parse_pull_request,
    render_summary,
    verify_signature,
)

SECRET = "shhh"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestSignature:
    def test_a_correctly_signed_body_passes(self) -> None:
        body = b'{"action":"opened"}'
        verify_signature(body, _sign(body), SECRET)

    def test_a_forged_signature_is_refused(self) -> None:
        """A forged webhook can start a scan against any repository we can reach."""
        with pytest.raises(WebhookError) as exc:
            verify_signature(b'{"action":"opened"}', _sign(b"other"), SECRET)
        assert exc.value.code == "bad_signature"

    def test_an_unsigned_request_is_refused(self) -> None:
        with pytest.raises(WebhookError) as exc:
            verify_signature(b"{}", None, SECRET)
        assert exc.value.code == "missing_signature"

    def test_a_missing_secret_fails_closed(self) -> None:
        """Misconfiguration must refuse everything rather than accept everything."""
        body = b"{}"
        with pytest.raises(WebhookError) as exc:
            verify_signature(body, _sign(body), "")
        assert exc.value.code == "no_secret"

    def test_the_raw_body_is_what_is_signed(self) -> None:
        """Reserialising before checking changes the bytes and breaks every signature."""
        payload = {"action": "opened", "number": 1}
        raw = json.dumps(payload).encode()
        verify_signature(raw, _sign(raw), SECRET)
        reserialised = json.dumps(json.loads(raw), indent=2).encode()
        with pytest.raises(WebhookError):
            verify_signature(reserialised, _sign(raw), SECRET)


class TestEventParsing:
    def _payload(self, **over):  # type: ignore[no-untyped-def]
        base = {
            "action": "opened",
            "number": 7,
            "repository": {"full_name": "acme/app", "clone_url": "https://github.com/acme/app.git"},
            "pull_request": {
                "number": 7,
                "head": {
                    "sha": "abc123",
                    "repo": {"clone_url": "https://github.com/acme/app.git"},
                },
            },
            "installation": {"id": 42},
        }
        base.update(over)
        return base

    def test_a_fork_is_scanned_as_the_fork(self) -> None:
        """The head repo, not the base — otherwise we scan code that is not being proposed."""
        payload = self._payload()
        payload["pull_request"]["head"]["repo"]["clone_url"] = "https://github.com/fork/app.git"
        assert parse_pull_request(payload).clone_url == "https://github.com/fork/app.git"

    def test_a_new_push_to_an_open_pr_rescans(self) -> None:
        """`synchronize` is the case that matters most: the diff changed, so the answer may have."""
        assert parse_pull_request(self._payload(action="synchronize")).should_scan

    def test_a_label_change_does_not_rescan(self) -> None:
        assert not parse_pull_request(self._payload(action="labeled")).should_scan


class TestCheckRunHonesty:
    def test_partial_coverage_is_neutral_not_success(self) -> None:
        """A green check on a repository we know nothing about is a claim we cannot support, and
        it is the one a reviewer acts on without reading further."""
        summary = CheckRunSummary(findings=0, suppressed=0, uncovered=3, unresolved=0)
        assert summary.conclusion == "neutral"

    def test_full_coverage_with_nothing_found_is_success(self) -> None:
        assert CheckRunSummary(0, suppressed=12, uncovered=0, unresolved=0).conclusion == "success"

    def test_findings_require_action(self) -> None:
        assert CheckRunSummary(2, 0, 0, 0).conclusion == "action_required"

    def test_uncovered_components_are_stated_not_omitted(self) -> None:
        body = render_summary(CheckRunSummary(0, 0, uncovered=4, unresolved=0), [])
        assert "not covered" in body
        assert "not that they are healthy" in body

    def test_unpinned_manifests_are_stated(self) -> None:
        body = render_summary(CheckRunSummary(0, 0, 0, unresolved=2), [])
        assert "without pinning" in body

    def test_the_suppressed_count_is_shown(self) -> None:
        """A scan that narrowed everything away must not look like one that found nothing."""
        body = render_summary(CheckRunSummary(0, suppressed=41, uncovered=0, unresolved=0), [])
        assert "41" in body and "suppressed" in body

    def test_a_finding_carries_its_citation(self) -> None:
        body = render_summary(
            CheckRunSummary(1, 0, 0, 0),
            [{"component": "lodash", "title": "prototype pollution", "reachable": True,
              "citations": ["ver-1"], "detail": "Referenced at a.js:3"}],
        )
        assert "lodash" in body and "ver-1" in body and "a.js:3" in body

    def test_undetermined_reachability_is_marked(self) -> None:
        body = render_summary(
            CheckRunSummary(1, 0, 0, 0),
            [{"component": "x", "title": "t", "reachable": None, "citations": []}],
        )
        assert "undetermined" in body


class TestWebhookRoute:
    """The route, not just the helpers. This is the surface an attacker reaches."""

    def _client(self, engine, monkeypatch):  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from bugmine.api import create_app

        monkeypatch.setenv("BUGMINE_GITHUB_WEBHOOK_SECRET", SECRET)
        return TestClient(create_app(engine))

    def _pr_body(self, action: str = "opened") -> bytes:
        return json.dumps({
            "action": action,
            "number": 7,
            "repository": {"full_name": "acme/app", "clone_url": "https://github.com/acme/app.git"},
            "pull_request": {
                "number": 7,
                "head": {"sha": "abc", "repo": {"clone_url": "https://github.com/acme/app.git"}},
            },
            "installation": {"id": 999},
        }).encode()

    def test_an_unsigned_webhook_is_rejected(self, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        client = self._client(engine, monkeypatch)
        r = client.post("/v1/hooks/github", content=self._pr_body(),
                        headers={"X-GitHub-Event": "pull_request"})
        assert r.status_code == 401

    def test_a_forged_signature_is_rejected(self, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        client = self._client(engine, monkeypatch)
        body = self._pr_body()
        r = client.post("/v1/hooks/github", content=body, headers={
            "X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(b"different")})
        assert r.status_code == 401

    def test_an_unmapped_installation_is_refused_not_provisioned(
        self, engine, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """Installing the app must not create a tenant, or it becomes the self-serve signup the
        tenancy model forbids — and a guessed mapping attributes one customer's scan to another.
        """
        client = self._client(engine, monkeypatch)
        monkeypatch.delenv("BUGMINE_GITHUB_INSTALLATIONS", raising=False)
        body = self._pr_body()
        r = client.post("/v1/hooks/github", content=body, headers={
            "X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)})
        assert r.status_code == 200
        assert r.json()["unmapped_installation"] == 999

    def test_a_ping_is_answered(self, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """GitHub sends this when the hook is created; failing it looks like a broken app."""
        client = self._client(engine, monkeypatch)
        body = b'{"zen":"hi"}'
        r = client.post("/v1/hooks/github", content=body, headers={
            "X-GitHub-Event": "ping", "X-Hub-Signature-256": _sign(body)})
        assert r.status_code == 200 and r.json()["pong"] is True

    def test_a_label_change_is_ignored_without_scanning(
        self, engine, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        client = self._client(engine, monkeypatch)
        body = self._pr_body(action="labeled")
        r = client.post("/v1/hooks/github", content=body, headers={
            "X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)})
        assert r.json()["ignored"] == "labeled"


class TestCheckRunPublishing:
    def test_the_installation_is_carried_into_the_job(
        self, engine, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """Without it the scan runs and the pull request never hears the result — the analyser
        cannot mint a token it has no installation id for."""
        import inspect

        from bugmine.api import app as app_module

        source = inspect.getsource(app_module.github_webhook)
        assert '"installation_id": pr.installation_id' in source
        assert '"repo_full_name": pr.repo_full_name' in source

    def test_a_reporting_failure_does_not_lose_the_scan(self) -> None:
        """Findings are already written and readable through the API. Raising here would fail
        the whole analysis and lose the scan over a reporting step."""
        import inspect

        from bugmine.worker import app as worker

        source = inspect.getsource(worker._publish_check_run)
        assert "except Exception:" in source
        assert "logger.exception" in source
