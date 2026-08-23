"""SSRF validation for tenant-supplied URLs.

Nearly every test here asserts a *refusal*. The permitted case is one line; the value is in
what is turned away, because a tenant-configurable source means our infrastructure fetches an
address someone else chose.
"""

from __future__ import annotations

import pytest
from bugmine.urlguard import check

FAKE_DNS = {
    "api.github.com": ["140.82.121.6"],
    "metadata.google.internal": ["169.254.169.254"],
    "localhost": ["127.0.0.1"],
    "internal.corp": ["10.1.2.3"],
    "split.test": ["93.184.216.34", "192.168.1.1"],
    "public.test": ["93.184.216.34"],
}


def resolver(host: str) -> list[str]:
    if host not in FAKE_DNS:
        raise OSError("NXDOMAIN")
    return FAKE_DNS[host]


def _check(url: str):  # type: ignore[no-untyped-def]
    # An explicit resolver, so these assert the rules rather than real DNS.
    return check(url, resolver=resolver)


class TestPermitted:
    def test_a_public_https_source_is_allowed(self) -> None:
        assert _check("https://api.github.com/repos/x/y/releases") is None


class TestRefused:
    def test_the_metadata_endpoint(self) -> None:
        """The attack this exists for: a service account token in an artifact the tenant reads."""
        r = _check("https://metadata.google.internal/computeMetadata/v1/instance/")
        assert r and r.code == "address_not_permitted"

    def test_loopback(self) -> None:
        assert _check("https://localhost/admin").code == "address_not_permitted"

    def test_private_ranges(self) -> None:
        assert _check("https://internal.corp/secrets").code == "address_not_permitted"

    @pytest.mark.parametrize("literal", ["169.254.169.254", "127.0.0.1", "10.0.0.1", "192.168.1.1"])
    def test_ip_literals_are_checked_the_same_way(self, literal: str) -> None:
        """Denying by address family, not by name — a literal skips DNS entirely."""
        r = check(f"https://{literal}/x", resolver=lambda h: [h])
        assert r and r.code == "address_not_permitted"

    def test_plaintext_http(self) -> None:
        assert _check("http://api.github.com/x").code == "scheme_not_permitted"

    @pytest.mark.parametrize("scheme", ["file", "gopher", "ftp", "data"])
    def test_other_schemes(self, scheme: str) -> None:
        assert _check(f"{scheme}://api.github.com/x").code == "scheme_not_permitted"

    def test_credentials_in_the_url(self) -> None:
        """Also a common way to disguise the real host from a human reviewer."""
        assert _check("https://user:pw@public.test/x").code == "credentials_in_url"

    def test_a_host_resolving_to_both_public_and_private_is_refused(self) -> None:
        """Otherwise it is fetchable by whichever address the client happens to pick."""
        assert _check("https://split.test/x").code == "address_not_permitted"

    def test_an_unresolvable_host(self) -> None:
        assert _check("https://nope.invalid/x").code == "host_unresolvable"

    def test_an_over_long_url(self) -> None:
        assert _check("https://public.test/" + "a" * 3000).code == "url_too_long"

    def test_a_missing_host(self) -> None:
        assert _check("https:///path").code in {"host_missing", "host_unresolvable"}
