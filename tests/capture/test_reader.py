"""Tests for the Reader as an MCP client, against the real server code in memory."""

import asyncio
from typing import Any

import httpx
import pytest
from mcp.client import Client
from pydantic import SecretStr

from synthora.capture.reader import TOOL_FOR_SOURCE_TYPE, ExtractionClient
from synthora.config import ExtractionConfig
from synthora.domain import ExtractedSource, ExtractionError, SourceType
from synthora.extraction import mcp_server

ARTICLE_HTML = (
    "<html><head><title>T</title></head><body><article><p>"
    + "Real words here. " * 30
    + "</p></article></body></html>"
)


class FakeSettings:
    google_api_key = SecretStr("FAKE-YOUTUBE-KEY")
    github_token = SecretStr("FAKE-GITHUB-TOKEN")
    extraction = ExtractionConfig(timeout_seconds=20)


@pytest.fixture
def requests_seen(monkeypatch) -> list[str]:
    """Run the Extraction Server against a fake web; returns the URLs it fetched."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "www.googleapis.com":
            return httpx.Response(
                200, json={"items": [{"snippet": {"title": "V", "description": "D"}}]}
            )
        if request.url.path == "/lnkd":  # a lnkd.in-style interstitial page
            return httpx.Response(
                200,
                text='<html><body><a data-tracking-control-name="external_url_click" '
                'href="https://github.com/a/b">go</a></body></html>',
                headers={"content-type": "text/html"},
            )
        if request.url.path == "/short":
            return httpx.Response(
                200,
                text="<html><body>hi</body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200, text=ARTICLE_HTML, headers={"content-type": "text/html"}
        )

    monkeypatch.setattr(
        mcp_server,
        "make_client",
        lambda timeout_seconds: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    monkeypatch.setattr(mcp_server, "Settings", FakeSettings)
    return seen


def call(method: str, *args) -> Any:
    """Call one ExtractionClient method against the in-memory server.

    Returns the result, or the ExtractionError it raised.
    """

    async def go():
        async with ExtractionClient(mcp_server.mcp) as reader:
            try:
                return await getattr(reader, method)(*args)
            except ExtractionError as e:
                return e

    return asyncio.run(go())


def read(url: str, source_type: SourceType) -> ExtractedSource | ExtractionError:
    return call("read", url, source_type)


def test_every_tool_in_the_routing_table_exists_on_the_server(requests_seen):
    """Catches a typo in a tool name on either side."""

    async def go():
        async with Client(mcp_server.mcp) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert set(TOOL_FOR_SOURCE_TYPE.values()) <= asyncio.run(go())


@pytest.mark.parametrize(
    "source_type, url, tool",
    [
        (SourceType.WEB_ARTICLE, "https://example.com/a", "extract_article"),
        (
            SourceType.LINKEDIN_POST,
            "https://www.linkedin.com/posts/x",
            "extract_with_defuddle",
        ),
        (SourceType.YOUTUBE_VIDEO, "https://youtu.be/abc", "read_youtube_metadata"),
    ],
)
def test_each_source_type_uses_its_tool(requests_seen, source_type, url, tool):
    assert read(url, source_type).tool == tool


def test_tool_failure_becomes_extraction_error_without_prefix(requests_seen):
    error = read("https://example.com/short", SourceType.WEB_ARTICLE)
    assert isinstance(error, ExtractionError)
    assert str(error).startswith("trafilatura found no main text")
    assert requests_seen == [
        "https://example.com/short"
    ]  # one tool, one fetch, no fallback


def test_resolve_returns_the_real_destination(requests_seen):
    assert call("resolve", "https://example.com/lnkd") == "https://github.com/a/b"


def test_resolve_failure_becomes_extraction_error(requests_seen):
    error = call("resolve", "https://example.com/short")  # a page with no external link
    assert isinstance(error, ExtractionError)
    assert str(error) == "no external link found on the lnkd.in page"


def test_reading_before_start_is_a_clear_error():
    with pytest.raises(RuntimeError, match="call start"):
        asyncio.run(
            ExtractionClient(mcp_server.mcp).read(
                "https://a.com", SourceType.WEB_ARTICLE
            )
        )
