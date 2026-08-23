"""Deterministic extraction from structured feeds.

The bias is toward silence. A marker that is merely plausible produces a record nobody can
trust, and precision is the product's entire claim — so a release that does not clearly
announce a defect is skipped rather than guessed at.
"""

from __future__ import annotations

import json

from bugmine.models import BugType
from bugmine.worker.structured import extract_github_releases, looks_like_github_releases


def _feed(*releases: dict) -> str:
    base = {"tag_name": "v1.0.0", "name": "v1.0.0", "body": "", "html_url": "https://x.test"}
    return json.dumps([{**base, **r} for r in releases])


class TestDetection:
    def test_recognises_a_releases_feed(self) -> None:
        assert looks_like_github_releases(_feed({}))

    def test_rejects_prose(self) -> None:
        assert not looks_like_github_releases("# Changelog\n\n- fixed a thing")

    def test_rejects_unrelated_json(self) -> None:
        assert not looks_like_github_releases('{"items": [1, 2, 3]}')

    def test_shape_not_url_decides(self) -> None:
        """A misconfigured source must fall through to the model, not be misparsed."""
        assert not looks_like_github_releases('[{"tag_name": "v1"}]'), "body is required too"


class TestClassification:
    def test_breaking_change_is_recognised(self) -> None:
        r = extract_github_releases(
            _feed({"body": "## Breaking Changes\n- removed the old API"}),
            component_ref="lib",
        )
        assert [b.bug_type for b in r.bugs] == [BugType.BREAKING_CHANGE]

    def test_deprecation_is_recognised(self) -> None:
        r = extract_github_releases(
            _feed({"body": "Deprecated the legacy client."}), component_ref="lib"
        )
        assert [b.bug_type for b in r.bugs] == [BugType.DEPRECATION]

    def test_cve_is_security(self) -> None:
        r = extract_github_releases(_feed({"body": "Fixes CVE-2026-1234."}), component_ref="lib")
        assert [b.bug_type for b in r.bugs] == [BugType.SECURITY]

    def test_a_feature_release_is_skipped(self) -> None:
        """Most releases describe features. Skipping is the expected outcome, not a failure."""
        r = extract_github_releases(
            _feed({"body": "Added a new helper and improved docs."}), component_ref="lib"
        )
        assert r.bugs == [] and r.skipped == 1

    def test_most_specific_marker_wins(self) -> None:
        r = extract_github_releases(
            _feed({"body": "Breaking changes, and fixes a regression."}), component_ref="lib"
        )
        assert r.bugs[0].bug_type is BugType.BREAKING_CHANGE

    def test_drafts_are_ignored(self) -> None:
        r = extract_github_releases(
            _feed({"draft": True, "body": "Breaking changes"}), component_ref="lib"
        )
        assert r.bugs == []


class TestVersions:
    def test_version_comes_from_the_tag(self) -> None:
        r = extract_github_releases(
            _feed({"tag_name": "v2.13.0", "body": "Breaking changes"}), component_ref="lib"
        )
        assert r.bugs[0].applicability["introduced_in"] == "2.13.0"

    def test_prerelease_versions_are_kept_whole(self) -> None:
        r = extract_github_releases(
            _feed({"tag_name": "v2.14.0a1", "body": "Breaking changes"}), component_ref="lib"
        )
        assert r.bugs[0].applicability["introduced_in"].startswith("2.14.0")

    def test_an_unparseable_tag_yields_no_version_rather_than_a_wrong_one(self) -> None:
        r = extract_github_releases(
            _feed({"tag_name": "nightly", "name": "nightly", "body": "Breaking changes"}),
            component_ref="lib",
        )
        assert r.bugs[0].applicability["introduced_in"] is None

    def test_evidence_url_is_carried(self) -> None:
        r = extract_github_releases(
            _feed({"body": "Breaking changes", "html_url": "https://gh.test/r/1"}),
            component_ref="lib",
        )
        assert r.bugs[0].evidence_url == "https://gh.test/r/1"


class TestLargeFeeds:
    def test_a_large_feed_still_parses(self) -> None:
        """Regression: a 1.2 MB releases feed was truncated at read time, became invalid JSON,
        and silently fell through to the model — which is more expensive and, in the deployed
        environment, unavailable. Truncation belongs at the model call, not at storage read.
        """
        big_body = "Breaking changes\n" + ("filler " * 2000)
        feed = _feed(*[{"tag_name": f"v1.{i}.0", "body": big_body} for i in range(60)])
        assert len(feed) > 500_000, "fixture must exceed the old truncation limit"
        assert looks_like_github_releases(feed)
        r = extract_github_releases(feed, component_ref="lib")
        assert len(r.bugs) == 60

    def test_truncated_json_is_not_mistaken_for_a_feed(self) -> None:
        """If something does truncate upstream, detection must decline rather than misparse."""
        feed = _feed({"body": "Breaking changes"})
        assert not looks_like_github_releases(feed[: len(feed) // 2])


class TestDirection:
    """Which way round a record points.

    A release note mentions a defect for one of two opposite reasons: the release introduces it,
    or the release fixes it. Recording a fix as an introduction inverts the record — every
    version that is actually safe gets flagged and every broken one gets cleared, which is
    strictly worse than having no record at all. Each test here is a shape that was, or would
    have been, stored backwards.
    """

    def test_security_fix_is_recorded_as_fixed_not_introduced(self) -> None:
        r = extract_github_releases(
            _feed({"tag_name": "1.2.3", "body": "### Security\n* Fix CVE-2026-1234 in headers"}),
            component_ref="lib",
        )
        assert len(r.bugs) == 1
        applicability = r.bugs[0].applicability
        assert applicability["fixed_in"] == "1.2.3"
        assert applicability["introduced_in"] is None

    def test_dropped_support_is_recorded_as_introduced(self) -> None:
        r = extract_github_releases(
            _feed({"tag_name": "v2.14.0", "body": "#### Changes\n* Drop support for Python 3.9"}),
            component_ref="pydantic",
        )
        assert len(r.bugs) == 1
        applicability = r.bugs[0].applicability
        assert applicability["introduced_in"] == "2.14.0"
        assert applicability["fixed_in"] is None

    def test_non_breaking_changes_is_not_a_breaking_change(self) -> None:
        """Release notes routinely say "considered non-breaking changes". Matching that phrase
        files a breaking-change record against a release whose text says the opposite."""
        r = extract_github_releases(
            _feed({"body": "These are considered non-breaking changes per our version policy."}),
            component_ref="pydantic",
        )
        assert r.bugs == []

    def test_ambiguous_marker_without_a_heading_is_dropped(self) -> None:
        """"regression" reads identically in "fixes a regression" and "introduces a regression".
        With no heading to resolve it, the match is dropped rather than guessed."""
        r = extract_github_releases(
            _feed({"body": "### Internal\n* Accept any base test failure as regression"}),
            component_ref="fastapi",
        )
        assert r.bugs == []
        assert r.unresolved == 1

    def test_regression_under_a_fixes_heading_is_a_fix(self) -> None:
        r = extract_github_releases(
            _feed({"tag_name": "0.9.1", "body": "### Bug Fixes\n* Fix a regression in parsing"}),
            component_ref="lib",
        )
        assert len(r.bugs) == 1
        assert r.bugs[0].bug_type is BugType.FUNCTIONAL
        assert r.bugs[0].applicability["fixed_in"] == "0.9.1"

    def test_title_is_the_defect_not_the_release_name(self) -> None:
        """A title of "v2.14.0a1" tells a reader nothing about what broke."""
        r = extract_github_releases(
            _feed({
                "tag_name": "v2.14.0a1",
                "name": "v2.14.0a1",
                "body": (
                    "#### Changes\n"
                    "* Remove support for `eval_type_backport()` by @v in [#1](x)"
                ),
            }),
            component_ref="pydantic",
        )
        assert r.bugs[0].title == "Remove support for eval_type_backport()"

    def test_one_record_per_defect_not_per_release(self) -> None:
        r = extract_github_releases(
            _feed({
                "tag_name": "v3.0.0",
                "body": "## Breaking Changes\n- removed the old API\n- dropped support for py38",
            }),
            component_ref="lib",
        )
        assert len(r.bugs) == 2
