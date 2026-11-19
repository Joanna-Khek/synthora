"""Tests for the Extraction Server, run in memory with a fake web.

`Client(mcp)` talks to the server object directly: no subprocess, no network.
"""

import asyncio

import httpx
import pytest
from mcp.client import Client
from pydantic import SecretStr

from synthora.server.extraction import mcp_server
from synthora.config import ExtractionConfig

PARAGRAPH = (
    "Every tool that is powerful enough to help is powerful enough to hurt. " * 10
)
ARTICLE_HTML = f"<html><head><title>Use the saw</title></head><body><article><p>{PARAGRAPH}</p></article></body></html>"


class FakeSettings:
    google_api_key = SecretStr("K")
    github_token = None
    extraction = ExtractionConfig(timeout_seconds=20)


@pytest.fixture
def fake_deps(monkeypatch):
    """Make the server's lifespan build a fake web and fake keys instead of real ones."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/dead":
            return httpx.Response(404)
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


def call(tool: str, arguments: dict):
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(go())


def test_lists_all_extractors(fake_deps):
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.list_tools()

    names = {t.name for t in asyncio.run(go()).tools}
    assert names == {
        "extract_article",
        "extract_with_defuddle",
        "read_og_meta",
        "read_github_repo",
        "read_youtube_metadata",
    }


def test_tool_returns_structured_extracted_source(fake_deps):
    result = call("extract_article", {"url": "https://example.com/saw"})
    assert not result.is_error
    assert result.structured_content["tool"] == "extract_article"
    assert result.structured_content["title"] == "Use the saw"
    assert "powerful enough to hurt" in result.structured_content["text"]


def test_extraction_error_reaches_the_client_as_a_readable_tool_error(fake_deps):
    result = call("extract_article", {"url": "https://example.com/dead"})
    assert result.is_error
    assert (
        "answered HTTP 404" in result.content[0].text
    )  # our message, not "Error executing tool"


def test_missing_url_is_rejected_by_the_schema(fake_deps):
    result = call("extract_article", {})
    assert result.is_error
