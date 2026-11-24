"""Tests for read_youtube_metadata. A fake YouTube API; the key never leaves a header."""

import asyncio

import httpx
import pytest

from synthora.domain import ExtractionError
from synthora.extraction.fetch import make_client
from synthora.extraction.youtube import read_youtube_metadata, video_id_from_url

VIDEO_URL = "https://youtu.be/abc123XYZ_-"
VIDEO_JSON = {
    "items": [
        {
            "snippet": {
                "title": "Building effective agents",
                "channelTitle": "Anthropic",
                "publishedAt": "2025-01-01T00:00:00Z",
                "description": "How to build agents that work.",
                "tags": ["agents", "llm"],
            }
        }
    ]
}


def fake_youtube(
    requests_seen: list | None = None, status: int = 200, body: dict = VIDEO_JSON
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests_seen is not None:
            requests_seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://youtu.be/abc123XYZ_-", id="short"),
        pytest.param("https://youtu.be/abc123XYZ_-?si=tracking", id="short-with-si"),
        pytest.param("https://www.youtube.com/watch?v=abc123XYZ_-&t=42s", id="watch"),
        pytest.param(
            "https://m.youtube.com/watch?feature=share&v=abc123XYZ_-",
            id="mobile-v-not-first",
        ),
        pytest.param("https://www.youtube.com/shorts/abc123XYZ_-", id="shorts"),
        pytest.param("https://www.youtube.com/live/abc123XYZ_-", id="live"),
    ],
)
def test_video_id_from_url(url):
    assert video_id_from_url(url) == "abc123XYZ_-"


def test_channel_page_has_no_video_id():
    with pytest.raises(ExtractionError, match="no video id"):
        video_id_from_url("https://www.youtube.com/@anthropic")


def test_reads_snippet():
    r = asyncio.run(read_youtube_metadata(fake_youtube(), VIDEO_URL, "KEY"))
    assert r.tool == "read_youtube_metadata"
    assert r.title == "Building effective agents"
    assert r.author == "Anthropic"
    assert r.text == "How to build agents that work."
    assert r.url == "https://www.youtube.com/watch?v=abc123XYZ_-"  # canonical form
    assert r.extra["tags"] == "agents, llm"


def test_api_key_goes_in_header_never_in_url():
    seen: list = []
    asyncio.run(read_youtube_metadata(fake_youtube(seen), VIDEO_URL, "SECRET-KEY"))
    (request,) = seen  # exactly one API call (1 quota unit)
    assert request.headers["x-goog-api-key"] == "SECRET-KEY"
    assert "SECRET-KEY" not in str(request.url)
    assert request.url.params["id"] == "abc123XYZ_-"
    assert request.url.params["part"] == "snippet"


def test_empty_items_means_failed_capture():
    """The API answers 200 with no items for deleted/private videos (PRD FR-8a)."""
    with pytest.raises(ExtractionError, match="not found"):
        asyncio.run(
            read_youtube_metadata(fake_youtube(body={"items": []}), VIDEO_URL, "K")
        )


def test_quota_exceeded_is_a_clean_error():
    body = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
    with pytest.raises(ExtractionError, match="quotaExceeded"):
        asyncio.run(
            read_youtube_metadata(fake_youtube(status=403, body=body), VIDEO_URL, "K")
        )


@pytest.mark.live
def test_live_youtube():
    from synthora.config import Settings

    key = Settings().google_api_key.get_secret_value()

    async def go():
        async with make_client() as client:
            return await read_youtube_metadata(
                client, "https://youtu.be/dQw4w9WgXcQ", key
            )

    r = asyncio.run(go())
    assert r.author == "Rick Astley"
