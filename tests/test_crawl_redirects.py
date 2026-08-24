"""Redirects, and the fact that following one must not be a way around the URL guard.

A source that moves permanently used to fail on every sweep and escalate its backoff until it
dropped out of the catalog — over a URL that had been renamed and was naming its successor in
the very response we were discarding. Following redirects fixes that, and immediately creates
the more dangerous problem these tests exist for: `urlguard` checks the URL a tenant registered,
and a redirect is a URL a tenant did not register.
"""

from __future__ import annotations

import httpx
import pytest
from bugmine.worker import crawl


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _redirect_chain(hops: dict[str, str], body: bytes = b"final"):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in hops:
            return httpx.Response(301, headers={"location": hops[url]})
        return httpx.Response(200, content=body)

    return handler


def test_a_moved_source_is_followed_to_its_new_home() -> None:
    """The case from production: cloud.google.com now 301s to docs.cloud.google.com."""
    start = "https://cloud.google.com/vertex-ai/docs/deprecations"
    moved = "https://docs.cloud.google.com/vertex-ai/docs/deprecations"
    with _client(_redirect_chain({start: moved}, b"deprecation notes")) as http:
        response, final = crawl._get_following_redirects(http, start, timeout=5.0)
    assert response.content == b"deprecation notes"
    assert final == moved


def test_a_redirect_to_a_blocked_address_is_refused() -> None:
    """The reason httpx's own follow_redirects is unusable here.

    An allowlisted public URL that redirects to link-local metadata would otherwise be fetched
    with the guard never consulted — credentials to whoever registered the source.
    """
    start = "https://example.test/innocent"
    # https, not http: an http target is rejected by the scheme rule before the address is ever
    # examined, so that version of this test would pass while the address check was broken.
    target = "https://169.254.169.254/computeMetadata/v1/"
    with (
        _client(_redirect_chain({start: target})) as http,
        pytest.raises(ValueError, match="address_not_permitted"),
    ):
        crawl._get_following_redirects(http, start, timeout=5.0)


def test_a_redirect_cannot_downgrade_to_plaintext() -> None:
    """Sources are https by policy; a redirect must not be a way to get fetched over http."""
    start = "https://example.test/secure"
    with (
        _client(_redirect_chain({start: "http://example.test/secure"})) as http,
        pytest.raises(ValueError, match="scheme_not_permitted"),
    ):
        crawl._get_following_redirects(http, start, timeout=5.0)


def test_a_redirect_to_a_private_host_is_refused() -> None:
    start = "https://example.test/innocent"
    with (
        _client(_redirect_chain({start: "https://10.0.0.1/admin"})) as http,
        pytest.raises(ValueError, match="address_not_permitted"),
    ):
        crawl._get_following_redirects(http, start, timeout=5.0)


def test_a_redirect_loop_fails_rather_than_spinning() -> None:
    a, b = "https://example.test/a", "https://example.test/b"
    with (
        _client(_redirect_chain({a: b, b: a})) as http,
        pytest.raises(ValueError, match="loop"),
    ):
        crawl._get_following_redirects(http, a, timeout=5.0)


def test_a_long_redirect_chain_is_bounded() -> None:
    hops = {f"https://example.test/{i}": f"https://example.test/{i + 1}" for i in range(20)}
    with (
        _client(_redirect_chain(hops)) as http,
        pytest.raises(ValueError, match="more than"),
    ):
        crawl._get_following_redirects(http, "https://example.test/0", timeout=5.0)


def test_the_github_token_does_not_follow_a_redirect_off_github(monkeypatch) -> None:
    """Headers are re-derived per hop, so the credential is scoped to the host it belongs to.

    A redirect from api.github.com to an attacker's host would otherwise hand over the token —
    and it is a token with 5000 requests an hour against our account.
    """
    monkeypatch.setenv("BUGMINE_GITHUB_TOKEN", "ghp_secret")
    start = "https://api.github.com/repos/x/y/releases"
    elsewhere = "https://example.test/collect"
    seen: dict[str, dict[str, str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[str(request.url)] = dict(request.headers)
        if str(request.url) == start:
            return httpx.Response(301, headers={"location": elsewhere})
        return httpx.Response(200, content=b"ok")

    with _client(handler) as http:
        crawl._get_following_redirects(http, start, timeout=5.0)

    assert "ghp_secret" in seen[start].get("authorization", "")
    assert "authorization" not in seen[elsewhere]


def test_a_redirect_without_a_location_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301)

    with (
        _client(handler) as http,
        pytest.raises(ValueError, match="no Location"),
    ):
        crawl._get_following_redirects(http, "https://example.test/x", timeout=5.0)


def test_a_real_error_status_still_raises() -> None:
    """Following redirects must not swallow a 404. A source that has genuinely gone should be
    recorded as failing, not as fetched."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with (
        _client(handler) as http,
        pytest.raises(httpx.HTTPStatusError),
    ):
        crawl._get_following_redirects(http, "https://example.test/gone", timeout=5.0)
