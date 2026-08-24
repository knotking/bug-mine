"""Image reference parsing.

Every failure here is silent: a misparse does not raise, it pulls a different image, and the
scan reports confidently on something the user never asked about.
"""

from __future__ import annotations

import pytest
from bugmine.container.reference import parse


class TestDefaults:
    def test_a_bare_official_name_gets_library_and_latest(self) -> None:
        r = parse("python")
        assert (r.registry, r.repository, r.tag) == ("docker.io", "library/python", "latest")

    def test_a_tag_is_kept(self) -> None:
        assert parse("python:3.12").tag == "3.12"

    def test_a_user_repository_is_not_library_prefixed(self) -> None:
        assert parse("myteam/app:1").repository == "myteam/app"

    def test_docker_hub_api_is_not_served_from_docker_io(self) -> None:
        """A detail that costs an afternoon when missed: docker.io is the canonical name and
        registry-1.docker.io is where the API lives."""
        assert parse("python").api_host == "registry-1.docker.io"
        assert parse("ghcr.io/o/app").api_host == "ghcr.io"


class TestRegistryDetection:
    def test_a_dotted_first_segment_is_a_registry(self) -> None:
        r = parse("ghcr.io/owner/app:v2")
        assert (r.registry, r.repository) == ("ghcr.io", "owner/app")

    def test_localhost_is_a_registry_without_a_dot(self) -> None:
        assert parse("localhost/app").registry == "localhost"

    def test_an_undotted_first_segment_is_a_namespace_not_a_host(self) -> None:
        """`myteam/app` is a Docker Hub repo. Treating myteam as a registry would send the pull
        to a host that does not exist — or worse, one that does."""
        assert parse("myteam/app").registry == "docker.io"

    def test_a_registry_port_is_not_mistaken_for_a_tag(self) -> None:
        """Splitting on the first colon turns `localhost:5000/app` into a tag of `5000/app`."""
        r = parse("localhost:5000/app:v1")
        assert (r.registry, r.repository, r.tag) == ("localhost:5000", "app", "v1")

    def test_a_registry_port_with_no_tag(self) -> None:
        r = parse("registry.io:5000/team/app")
        assert (r.registry, r.repository, r.tag) == ("registry.io:5000", "team/app", "latest")


class TestDigests:
    D = "sha256:" + "a" * 64

    def test_a_digest_reference_is_pinned(self) -> None:
        r = parse(f"python@{self.D}")
        assert r.pinned and r.digest == self.D

    def test_a_digest_reference_gets_no_invented_tag(self) -> None:
        """`latest` on a digest reference would put a name in the record pointing somewhere
        else entirely."""
        assert parse(f"python@{self.D}").tag is None

    def test_a_tag_alone_is_not_pinned(self) -> None:
        """The premise of watching base images: python:3.12 is a different image week to week."""
        assert not parse("python:3.12").pinned

    def test_both_tag_and_digest_keep_both(self) -> None:
        r = parse(f"python:3.12@{self.D}")
        assert r.tag == "3.12" and r.digest == self.D

    def test_canonical_prefers_the_digest(self) -> None:
        assert parse(f"python:3.12@{self.D}").canonical == f"docker.io/library/python@{self.D}"


class TestRejection:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "python@sha256:short",
            "python@md5:" + "a" * 32,
            "python:",
            "python:bad tag",
            "python:" + "x" * 200,
        ],
    )
    def test_ambiguous_references_are_refused(self, bad: str) -> None:
        """Refused rather than guessed. A reference we cannot parse confidently is one we would
        pull from somewhere the user did not intend."""
        with pytest.raises(ValueError):
            parse(bad)
