"""Tests for the SSRF guard (PRD NFR-3, task 3.6). No network needed."""

import asyncio

import httpx
import pytest

from synthora.server.extraction import ssrf
from synthora.server.extraction.fetch import ExtractionError, fetch_html, make_client
from synthora.server.extraction.ssrf import BlockedURLError, check_url
from synthora.config import Settings

settings = Settings()

PAGE = "<html><body><p>hello</p></body></html>"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://127.0.0.1:5000/", id="loopback"),
        pytest.param("http://localhost/", id="localhost-name"),
        pytest.param("http://[::1]/", id="ipv6-loopback"),
        pytest.param("http://[::ffff:127.0.0.1]/", id="ipv4-mapped-ipv6"),
        pytest.param("http://169.254.169.254/latest/meta-data/", id="cloud-metadata"),
        pytest.param("http://10.0.0.5/", id="private-10"),
        pytest.param("http://172.16.0.1/", id="private-172"),
        pytest.param("http://192.168.1.1/admin", id="private-192"),
        pytest.param("http://100.64.0.1/", id="carrier-grade-nat"),
        pytest.param("http://0.0.0.0/", id="unspecified"),
        pytest.param("http://224.0.0.1/", id="multicast"),
        pytest.param("http://[fe80::1]/", id="ipv6-link-local"),
        pytest.param("file:///etc/passwd", id="file-scheme"),
        pytest.param("ftp://8.8.8.8/", id="ftp-scheme"),
    ],
)
def test_blocks_non_public_targets(url):
    with pytest.raises(BlockedURLError):
        asyncio.run(check_url(url))


def test_allows_a_public_ip():
    asyncio.run(check_url("https://8.8.8.8/"))  # no exception = allowed


def fake_dns(monkeypatch, table: dict[str, list[str]]) -> None:
    """Pretend DNS for the names in `table`; anything else (e.g. a literal IP) resolves for real."""
    real_resolve = ssrf.resolve

    async def resolve(host, port):
        if host in table:
            return table[host]
        return await real_resolve(host, port)

    monkeypatch.setattr(ssrf, "resolve", resolve)


def test_blocks_a_public_looking_name_that_resolves_to_a_private_ip(monkeypatch):
    fake_dns(monkeypatch, {"intranet.example.com": ["10.1.2.3"]})
    with pytest.raises(BlockedURLError, match="10.1.2.3"):
        asyncio.run(check_url("https://intranet.example.com/"))


def test_blocks_if_any_resolved_address_is_private(monkeypatch):
    fake_dns(monkeypatch, {"mixed.example.com": ["93.184.215.14", "127.0.0.1"]})
    with pytest.raises(BlockedURLError):
        asyncio.run(check_url("https://mixed.example.com/"))


def test_redirect_to_an_internal_address_is_blocked_before_it_is_sent(monkeypatch):
    """A public page that redirects to cloud metadata: the second hop never leaves."""
    fake_dns(monkeypatch, {"example.com": ["93.184.215.14"]})
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        if request.url.host == "example.com":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        return httpx.Response(
            200, text="SECRET CREDENTIALS", headers={"content-type": "text/html"}
        )

    async def go():
        async with make_client(
            timeout_seconds=settings.extraction.timeout_seconds,
            transport=httpx.MockTransport(handler),
        ) as client:
            return await fetch_html(client, "https://example.com/innocent")

    with pytest.raises(ExtractionError, match="non-public"):
        asyncio.run(go())
    assert sent == [
        "https://example.com/innocent"
    ]  # the metadata request was never sent


def test_public_page_still_works_through_the_guard(monkeypatch):
    fake_dns(monkeypatch, {"example.com": ["93.184.215.14"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    async def go():
        async with make_client(
            timeout_seconds=settings.extraction.timeout_seconds,
            transport=httpx.MockTransport(handler),
        ) as client:
            return await fetch_html(client, "https://example.com/")

    final_url, html = asyncio.run(go())
    assert html == PAGE
