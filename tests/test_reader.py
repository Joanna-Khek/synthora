"""Tests for the Reader's routing: each Source Type calls exactly one extractor."""

import asyncio

import httpx
import pytest

from synthora.schemas import SourceType
from synthora.server.extraction import ExtractionError
from synthora.server.reader import read_url

ARTICLE_HTML = (
    "<html><head><title>T</title></head><body><article><p>"
    + "Real words here. " * 30
    + "</p></article></body></html>"
)


def fake_client(
    body: str = ARTICLE_HTML, seen: list | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.host == "www.googleapis.com":
            return httpx.Response(
                200, json={"items": [{"snippet": {"title": "V", "description": "D"}}]}
            )
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


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
def test_each_source_type_uses_its_extractor(source_type, url, tool):
    result = asyncio.run(read_url(fake_client(), url, source_type, youtube_key="K"))
    assert result.tool == tool


def test_no_fallback_on_failure():
    """A page too short for trafilatura fails once; nothing else is tried."""
    seen: list = []
    with pytest.raises(ExtractionError, match="trafilatura"):
        asyncio.run(
            read_url(
                fake_client("<html><body>hi</body></html>", seen),
                "https://example.com/a",
                SourceType.WEB_ARTICLE,
                youtube_key="K",
            )
        )
    assert seen == ["https://example.com/a"]  # fetched exactly once
