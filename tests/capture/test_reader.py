"""Tests for the Reader as an MCP client, against the real server code in memory."""

import asyncio

import httpx
import pytest
from mcp.client import Client
from pydantic import SecretStr

from synthora.capture.reader import TOOL_FOR_SOURCE_TYPE, read_url
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


def read(url: str, source_type: SourceType) -> ExtractedSource | ExtractionError:
    """Returns the result, or the ExtractionError (caught INSIDE the client block).

    An exception that escapes `async with Client(...)` gets wrapped in an
    ExceptionGroup by anyio's task group, so we catch it before it gets there.
    """

    async def go():
        async with Client(mcp_server.mcp) as client:
            try:
                return await read_url(client, url, source_type)
            except ExtractionError as e:
                return e

    return asyncio.run(go())


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
