"""URL validation for tenant-supplied crawl sources.

A tenant-configurable source inverts the trust model: the tenant supplies a URL that *our*
infrastructure fetches, from inside our network, with our egress. That is a server-side request
forgery primitive, and it did not exist while sources were admin-only.

The realistic attack is not exotic. Point a source at
`http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token`
and the crawler fetches a service account token into an artifact the tenant can read back.

Three rules matter more than the rest:

**Resolve DNS, then check the address.** Validating the hostname alone loses to DNS rebinding —
`evil.test` can resolve to a public address at validation time and 169.254.169.254 at fetch
time. This narrows the window; it does not close it, which is why the network-layer deny in
Terraform exists as well.

**Deny by address family, not by name.** Blocklisting `metadata.google.internal` catches one
spelling of one target. Denying every private, loopback, link-local and reserved range catches
the class.

**Refuse redirects to a different host.** A permitted URL that 302s to a denied one is the same
attack with an extra hop.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"https"}
"""http is excluded deliberately: a plaintext fetch of a tenant-supplied URL is both
interceptable and, in practice, a sign the source is not what it claims to be."""

MAX_URL_LENGTH = 2048


@dataclass(frozen=True)
class Rejection:
    code: str
    message: str


def _address_is_permitted(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local  # 169.254.0.0/16 — the metadata endpoint
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def check(url: str, *, resolver=None) -> Rejection | None:  # type: ignore[no-untyped-def]
    """Return a Rejection if the URL must not be fetched, or None if it may be.

    `resolver` is injectable so the rules can be tested without DNS, and so a caller can supply
    a resolver that pins the resolved address for the subsequent fetch — which is the only way
    to actually close the rebinding window.
    """
    if len(url) > MAX_URL_LENGTH:
        return Rejection("url_too_long", "URL exceeds the maximum length.")

    try:
        parsed = urlparse(url)
    except ValueError:
        return Rejection("url_unparseable", "URL could not be parsed.")

    if parsed.scheme not in ALLOWED_SCHEMES:
        return Rejection("scheme_not_permitted", "Only https sources are accepted.")

    host = parsed.hostname
    if not host:
        return Rejection("host_missing", "URL has no host.")

    if parsed.username or parsed.password:
        # Credentials in a URL are both a leak and a common way to disguise the real host.
        return Rejection("credentials_in_url", "Credentials must not appear in the URL.")

    resolve = resolver or _resolve
    try:
        addresses = resolve(host)
    except OSError:
        return Rejection("host_unresolvable", f"Could not resolve {host}.")

    if not addresses:
        return Rejection("host_unresolvable", f"Could not resolve {host}.")

    # Every resolved address must be permitted. A host that returns one public and one private
    # address would otherwise be fetchable by whichever the client happened to pick.
    for ip in addresses:
        if not _address_is_permitted(ip):
            return Rejection(
                "address_not_permitted",
                f"{host} resolves to a disallowed address ({ip}).",
            )

    return None


def _resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})
