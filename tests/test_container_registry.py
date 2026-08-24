"""The registry client.

Everything here runs against a mock transport rather than a real registry: these are the
protocol details that are easy to get subtly wrong, and a test that needs the network would be
skipped exactly when it mattered.
"""

from __future__ import annotations

import gzip
import io
import tarfile

import httpx
import pytest
from bugmine.container.reference import parse
from bugmine.container.registry import (
    RegistryClient,
    RegistryError,
    _select_platform,
)

DIGEST = "sha256:" + "b" * 64
CONFIG_DIGEST = "sha256:" + "c" * 64
LAYER_DIGEST = "sha256:" + "d" * 64


def _layer_blob(files: dict[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue())


class TestPlatformSelection:
    def test_the_requested_platform_is_chosen_not_the_first(self) -> None:
        """A tag's index routinely leads with arm64. Scanning it for a service that runs on
        amd64 reports on software that will never execute, while looking like a clean run."""
        entries = [
            {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
        ]
        chosen = _select_platform(entries, ("linux", "amd64"))
        assert chosen is not None and chosen["digest"] == "sha256:amd"

    def test_a_missing_platform_is_none_rather_than_a_fallback(self) -> None:
        """Falling back to whatever is available would silently scan the wrong architecture."""
        entries = [{"digest": "x", "platform": {"os": "linux", "architecture": "s390x"}}]
        assert _select_platform(entries, ("linux", "amd64")) is None

    def test_windows_and_linux_are_not_interchangeable(self) -> None:
        entries = [{"digest": "x", "platform": {"os": "windows", "architecture": "amd64"}}]
        assert _select_platform(entries, ("linux", "amd64")) is None


class TestAuthentication:
    def test_an_anonymous_pull_fetches_a_token_and_retries(self) -> None:
        """Docker Hub 401s an unauthenticated manifest request. Without the token dance a
        perfectly public image reads as missing."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            seen.append(url)
            if "auth.example.test" in url:
                return httpx.Response(200, json={"token": "t0ken"})
            if request.headers.get("Authorization") != "Bearer t0ken":
                return httpx.Response(
                    401,
                    headers={
                        "www-authenticate": 'Bearer realm="https://auth.example.test/token",'
                        'service="registry.example.test"'
                    },
                )
            return httpx.Response(
                200,
                headers={"docker-content-digest": DIGEST},
                json={"mediaType": "application/vnd.oci.image.manifest.v1+json", "layers": []},
            )

        with RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c:
            digest, _ = c.resolve(parse("registry.example.test/team/app:1"))
        assert digest == DIGEST
        assert any("auth.example.test" in u for u in seen), "no token was fetched"

    def test_an_auth_realm_is_url_guarded(self) -> None:
        """The realm comes from the registry's own response — a host we do not control naming
        another host for us to call. It gets the same guard as any tenant-supplied URL."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                headers={"www-authenticate": 'Bearer realm="http://169.254.169.254/token"'},
            )

        with (
            RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c,
            pytest.raises(RegistryError, match="rejected"),
        ):
            c.resolve(parse("registry.example.test/team/app:1"))

    def test_a_realmless_challenge_fails_loudly(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, headers={"www-authenticate": "Bearer"})

        with (
            RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c,
            pytest.raises(RegistryError, match="auth realm"),
        ):
            c.resolve(parse("registry.example.test/team/app:1"))


class TestResolution:
    def _handler(self, manifest: dict, index: dict | None = None):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if index is not None and url.endswith("/manifests/1"):
                return httpx.Response(
                    200, headers={"docker-content-digest": "sha256:index"}, json=index
                )
            if "/manifests/" in url:
                return httpx.Response(
                    200, headers={"docker-content-digest": DIGEST}, json=manifest
                )
            if CONFIG_DIGEST in url:
                return httpx.Response(200, json={"config": {"Env": ["PATH=/usr/bin"]}})
            if LAYER_DIGEST in url:
                return httpx.Response(
                    200, content=_layer_blob({"var/lib/dpkg/status": b"Package: x\n"})
                )
            return httpx.Response(404)

        return handler

    MANIFEST = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": CONFIG_DIGEST},
        "layers": [{"digest": LAYER_DIGEST}],
    }

    def test_an_index_is_followed_to_a_concrete_manifest(self) -> None:
        index = {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"digest": DIGEST, "platform": {"os": "linux", "architecture": "amd64"}}
            ],
        }
        handler = self._handler(self.MANIFEST, index)
        with RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c:
            digest, manifest = c.resolve(parse("registry.example.test/team/app:1"))
        assert digest == DIGEST
        assert manifest["layers"][0]["digest"] == LAYER_DIGEST

    def test_an_index_without_the_platform_fails_rather_than_scanning_the_wrong_one(
        self,
    ) -> None:
        index = {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"digest": "sha256:x", "platform": {"os": "linux", "architecture": "arm64"}}
            ],
        }
        handler = self._handler(self.MANIFEST, index)
        with (
            RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c,
            pytest.raises(RegistryError, match="no linux/amd64"),
        ):
            c.resolve(parse("registry.example.test/team/app:1"))

    def test_pulling_yields_the_digest_config_and_layers(self) -> None:
        """The digest is the durable identity of a scan against a moving tag — recording it is
        what makes drift detectable next time."""
        handler = self._handler(self.MANIFEST)
        with RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c:
            image = c.pull(parse("registry.example.test/team/app:1"))
        assert image.digest == DIGEST
        assert image.path_dirs == ("usr/bin",)
        assert len(image.layers) == 1
        assert image.layer_digests == (LAYER_DIGEST,)

    def test_an_uncompressed_layer_is_handled(self) -> None:
        """Registries serve both gzipped and plain layers, and some report the wrong
        mediaType — so the bytes are sniffed rather than the descriptor trusted."""
        plain = io.BytesIO()
        with tarfile.open(fileobj=plain, mode="w") as t:
            info = tarfile.TarInfo("etc/x")
            info.size = 1
            t.addfile(info, io.BytesIO(b"y"))

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/manifests/" in url:
                return httpx.Response(
                    200,
                    headers={"docker-content-digest": DIGEST},
                    json={"config": {}, "layers": [{"digest": LAYER_DIGEST}]},
                )
            return httpx.Response(200, content=plain.getvalue())

        with RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c:
            image = c.pull(parse("registry.example.test/team/app:1"))
        assert [m.name for m in image.layers[0].getmembers()] == ["etc/x"]

    def test_too_many_layers_is_refused(self) -> None:
        from bugmine.container.registry import MAX_LAYERS

        many = {"config": {}, "layers": [{"digest": LAYER_DIGEST}] * (MAX_LAYERS + 1)}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"docker-content-digest": DIGEST}, json=many)

        with (
            RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c,
            pytest.raises(RegistryError, match="layers exceeds"),
        ):
            c.pull(parse("registry.example.test/team/app:1"))

    def test_a_missing_image_reports_the_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        with (
            RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c,
            pytest.raises(RegistryError, match="404"),
        ):
            c.resolve(parse("registry.example.test/team/app:1"))


class TestEgressGuard:
    def test_a_private_registry_address_is_refused(self) -> None:
        """A registry host is tenant-supplied, so it gets the same SSRF guard as a crawl URL."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        with (
            RegistryClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as c,
            pytest.raises(RegistryError, match="address_not_permitted"),
        ):
            c.resolve(parse("169.254.169.254/team/app:1"))
