"""Pulling an image from an OCI registry, without running any of it.

This is the egress half of container scanning and it sits where `crawl` and `scan_fetch` sit:
network access, no model (ADR-0005, NFR-42). What comes back is an archive chosen by whoever
controls the registry, so everything here treats it as hostile input — capped, streamed, and
never executed.

Registries are more awkward than their documentation suggests. Three things bite:

**Anonymous still means authenticated.** Docker Hub rejects an unauthenticated manifest request
with a 401 carrying a `WWW-Authenticate` header naming a token service. You fetch a token from
there — no credentials — and retry. Skipping this reads as "image not found".

**A tag usually resolves to an index, not an image.** Multi-architecture tags return a manifest
list, and picking the wrong entry scans an image that will never run on the target.

**The digest is the answer to a question nobody asked yet.** Resolving `python:3.12` yields a
digest, and that digest is the only durable identity the scan has. Recording it is what makes
"this tag moved" detectable later.
"""

from __future__ import annotations

import gzip
import io
import logging
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any

import httpx

from bugmine import urlguard
from bugmine.container.reference import ImageReference

logger = logging.getLogger("bugmine.container.registry")

INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
MANIFEST_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
ACCEPT = ", ".join(sorted(INDEX_TYPES | MANIFEST_TYPES))

MAX_LAYER_BYTES = 512 * 1024 * 1024
"""Compressed, per layer. A registry can serve an arbitrarily large blob and a decompression
bomb is cheap to build, so the cap is enforced while streaming rather than after."""

MAX_LAYERS = 128

DEFAULT_PLATFORM = ("linux", "amd64")


@dataclass
class PulledImage:
    reference: ImageReference
    digest: str
    """What the tag actually resolved to. The only durable identity a scan of a moving tag has,
    and what makes drift detectable on the next run."""
    config: dict[str, Any]
    layer_digests: tuple[str, ...] = ()
    layers: list[tarfile.TarFile] = field(default_factory=list)
    platform: tuple[str, str] = DEFAULT_PLATFORM

    @property
    def base_image_hint(self) -> str | None:
        """What the Dockerfile said it built on, when the builder recorded it.

        Best-effort by nature: it is a label, not a guarantee, and plenty of builders write
        nothing. Absent is reported as absent rather than inferred from the layer history,
        which would be a guess wearing the same field name as a fact.
        """
        labels = (self.config.get("config") or {}).get("Labels") or {}
        for key in ("org.opencontainers.image.base.name", "org.label-schema.base-image"):
            if labels.get(key):
                return str(labels[key])
        return None

    @property
    def entrypoint(self) -> tuple[str, ...]:
        cfg = self.config.get("config") or {}
        return tuple(cfg.get("Entrypoint") or ()) + tuple(cfg.get("Cmd") or ())

    @property
    def path_dirs(self) -> tuple[str, ...]:
        cfg = self.config.get("config") or {}
        for entry in cfg.get("Env") or []:
            if isinstance(entry, str) and entry.startswith("PATH="):
                return tuple(p.lstrip("/") for p in entry[5:].split(":") if p)
        return ("usr/local/bin", "usr/bin", "bin")


class RegistryError(RuntimeError):
    pass


def _auth_challenge(header: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', header or ""))


class RegistryClient:
    """A minimal read-only OCI registry client."""

    def __init__(self, *, timeout: float = 60.0, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._tokens: dict[str, str] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RegistryClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _url(self, ref: ImageReference, path: str) -> str:
        url = f"https://{ref.api_host}/v2/{ref.repository}/{path}"
        # The same guard the crawler uses. A registry host is tenant-supplied, so it gets the
        # same treatment as any other URL a tenant can point us at.
        if rejection := urlguard.check(url):
            raise RegistryError(f"{rejection.code}: {rejection.message}")
        return url

    def _get(self, ref: ImageReference, path: str, *, accept: str, stream: bool = False):  # type: ignore[no-untyped-def]
        url = self._url(ref, path)
        headers = {"Accept": accept}
        if token := self._tokens.get(ref.repository):
            headers["Authorization"] = f"Bearer {token}"

        request = self._client.build_request("GET", url, headers=headers)
        response = self._client.send(request, stream=stream)
        if response.status_code == 401:
            # Anonymous pulls still need a token. Without this the retry never happens and a
            # public image reads as missing.
            response.read()
            response.close()
            self._authenticate(ref, response.headers.get("www-authenticate", ""))
            headers["Authorization"] = f"Bearer {self._tokens.get(ref.repository, '')}"
            request = self._client.build_request("GET", url, headers=headers)
            response = self._client.send(request, stream=stream)

        if response.status_code >= 400:
            if stream:
                response.read()
            detail = response.text[:200]
            response.close()
            raise RegistryError(f"{response.status_code} from {url}: {detail}")
        return response

    def _authenticate(self, ref: ImageReference, challenge: str) -> None:
        params = _auth_challenge(challenge)
        realm = params.get("realm")
        if not realm:
            raise RegistryError(f"registry {ref.api_host} gave no auth realm")
        if rejection := urlguard.check(realm):
            raise RegistryError(f"auth realm rejected: {rejection.code}")
        token_response = self._client.get(
            realm,
            params={
                "service": params.get("service", ref.api_host),
                "scope": params.get("scope", f"repository:{ref.repository}:pull"),
            },
        )
        token_response.raise_for_status()
        body = token_response.json()
        token = body.get("token") or body.get("access_token")
        if not token:
            raise RegistryError("auth service returned no token")
        self._tokens[ref.repository] = str(token)

    def resolve(
        self, ref: ImageReference, *, platform: tuple[str, str] = DEFAULT_PLATFORM
    ) -> tuple[str, dict[str, Any]]:
        """Resolve a reference to a concrete image manifest and the digest naming it."""
        target = ref.digest or ref.tag
        response = self._get(ref, f"manifests/{target}", accept=ACCEPT)
        digest = response.headers.get("docker-content-digest", ref.digest or "")
        manifest = response.json()

        if manifest.get("mediaType") in INDEX_TYPES or "manifests" in manifest:
            chosen = _select_platform(manifest.get("manifests") or [], platform)
            if chosen is None:
                raise RegistryError(f"no {platform[0]}/{platform[1]} image in {ref.canonical}")
            digest = chosen["digest"]
            response = self._get(ref, f"manifests/{digest}", accept=ACCEPT)
            manifest = response.json()

        if not digest:
            raise RegistryError(f"registry returned no digest for {ref.canonical}")
        return digest, manifest

    def pull(
        self,
        ref: ImageReference,
        *,
        platform: tuple[str, str] = DEFAULT_PLATFORM,
        wanted=None,  # type: ignore[no-untyped-def]
    ) -> PulledImage:
        """Fetch the config and every layer, ready to flatten.

        Layers arrive gzipped and are decompressed into memory under a cap. Not to disk: a tar
        extracted to a filesystem is where path traversal turns into a written file, and there
        is no reason to take that risk to read a package database.
        """
        digest, manifest = self.resolve(ref, platform=platform)

        config_digest = (manifest.get("config") or {}).get("digest")
        config: dict[str, Any] = {}
        if config_digest:
            config = self._get(ref, f"blobs/{config_digest}", accept="*/*").json()

        descriptors = manifest.get("layers") or []
        if len(descriptors) > MAX_LAYERS:
            raise RegistryError(f"{len(descriptors)} layers exceeds the cap of {MAX_LAYERS}")

        image = PulledImage(
            reference=ref,
            digest=digest,
            config=config,
            layer_digests=tuple(d.get("digest", "") for d in descriptors),
            platform=platform,
        )
        for descriptor in descriptors:
            blob = self._blob(ref, descriptor["digest"])
            if blob is not None:
                image.layers.append(blob)
        return image

    def _blob(self, ref: ImageReference, digest: str) -> tarfile.TarFile | None:
        response = self._get(ref, f"blobs/{digest}", accept="*/*", stream=True)
        try:
            buffer = io.BytesIO()
            for chunk in response.iter_bytes(chunk_size=1024 * 256):
                buffer.write(chunk)
                if buffer.tell() > MAX_LAYER_BYTES:
                    logger.warning("layer %s exceeds the size cap; skipped", digest[:19])
                    return None
        finally:
            response.close()

        buffer.seek(0)
        raw = buffer.read()
        # Sniff rather than trust the descriptor's mediaType: registries serve both gzipped and
        # uncompressed layers, and some report the wrong one.
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return tarfile.open(fileobj=io.BytesIO(raw), mode="r")


def _select_platform(
    entries: list[dict[str, Any]], platform: tuple[str, str]
) -> dict[str, Any] | None:
    """Pick the right architecture from a manifest list.

    Not "the first one". A tag's index routinely leads with arm64, and scanning an arm64 image
    for a service that runs on amd64 reports on software that will never execute — while looking
    like a complete, successful scan.
    """
    os_name, arch = platform
    for entry in entries:
        p = entry.get("platform") or {}
        if p.get("os") == os_name and p.get("architecture") == arch:
            return entry
    return None
