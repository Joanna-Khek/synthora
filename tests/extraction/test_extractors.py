import asyncio
import shutil

import httpx
import pytest

from synthora.server.extraction import (
    ExtractionError,
    fetch_html,
    make_client,
    extract_article,
    extract_with_defuddle,
    html_extractors,
    read_og_meta,
)

from synthora.config import Settings

settings = Settings()

PARAGRAPH = (
    "Every tool that is powerful enough to help is powerful enough to hurt. " * 10
)
ARTICLE_HTML = f"""<html><head><title>Use the saw</title>
<meta name="author" content="Ada Lovelace"></head>
<body>
  <nav>Home | About | Subscribe</nav>
  <article><h1>Use the saw</h1><p>{PARAGRAPH}</p><p>{PARAGRAPH}</p></article>
  <footer>Copyright 2026</footer>
</body></html>"""
COOKIE_BANNER_HTML = "<html><body><p>We use cookies. Accept all?</p></body></html>"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js not installed"
)


def fake_client(
    body: str = ARTICLE_HTML,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
) -> httpx.AsyncClient:
    """An AsyncClient whose every request gets the same canned response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, headers={"content-type": content_type})

    # Transport is the part of httpx that sends bytes over the network.
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_follows_redirects_and_returns_final_url():
    """A short link (301) is followed, and the FINAL url is returned."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "lnkd.in":
            return httpx.Response(
                301, headers={"location": "https://www.linkedin.com/posts/abc"}
            )
        return httpx.Response(
            200, text=ARTICLE_HTML, headers={"content-type": "text/html"}
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    final_url, html = asyncio.run(fetch_html(client, "https://lnkd.in/p/xyz"))
    assert final_url == "https://www.linkedin.com/posts/abc"
    assert "Use the saw" in html


def test_make_client_follows_redirects():
    """httpx does not follow redirects by default; our client must."""
    assert (
        make_client(
            timeout_seconds=settings.extraction.timeout_seconds
        ).follow_redirects
        is True
    )


@pytest.mark.parametrize("status", [403, 404, 500])
def test_fetch_error_status_becomes_extraction_error(status):
    with pytest.raises(ExtractionError, match=str(status)):
        asyncio.run(fetch_html(fake_client(status=status), "https://example.com"))


def test_fetch_refuses_non_html():
    with pytest.raises(ExtractionError, match="not a web page"):
        asyncio.run(
            fetch_html(
                fake_client(content_type="application/pdf"), "https://example.com/a.pdf"
            )
        )


def test_fetch_network_failure_becomes_extraction_error():
    """DNS failures, timeouts etc. come out as ExtractionError, not raw httpx errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError, match="ConnectError"):
        asyncio.run(fetch_html(client, "https://example.com"))


def test_extract_article_keeps_main_text_and_drops_page_chrome():
    result = asyncio.run(extract_article(fake_client(), "https://example.com/saw"))
    assert result.tool == "extract_article"
    assert result.title == "Use the saw"
    assert result.author == "Ada Lovelace"
    assert "powerful enough to hurt" in result.text
    assert "Subscribe" not in result.text  # nav removed
    assert "<p>" not in result.text  # never raw HTML


def test_extract_article_rejects_a_page_with_only_a_cookie_banner():
    """trafilatura 'succeeds' on tiny pages; MIN_TEXT_CHARS turns that into a failure."""
    with pytest.raises(ExtractionError, match="no main text"):
        asyncio.run(
            extract_article(fake_client(body=COOKIE_BANNER_HTML), "https://example.com")
        )


@needs_node
def test_defuddle_keeps_main_text():
    result = asyncio.run(
        extract_with_defuddle(fake_client(), "https://example.com/saw")
    )
    assert result.tool == "extract_with_defuddle"
    assert result.title == "Use the saw"
    assert "powerful enough to hurt" in result.text
    assert "<p>" not in result.text


@needs_node
def test_defuddle_rejects_a_page_with_only_a_cookie_banner():
    with pytest.raises(ExtractionError, match="no main text"):
        asyncio.run(
            extract_with_defuddle(
                fake_client(body=COOKIE_BANNER_HTML), "https://example.com"
            )
        )


@needs_node
def test_defuddle_missing_cli_is_a_clean_error(monkeypatch, tmp_path):
    """If node_modules is missing, we get ExtractionError, not a crash."""
    monkeypatch.setattr(html_extractors, "DEFUDDLE_CLI", tmp_path / "nope.js")
    with pytest.raises(ExtractionError, match="Defuddle failed"):
        asyncio.run(extract_with_defuddle(fake_client(), "https://example.com"))


@pytest.mark.live
def test_live_linkedin_short_link_via_defuddle():
    async def go():
        async with make_client(
            timeout_seconds=settings.extraction.timeout_seconds
        ) as client:
            return await extract_with_defuddle(client, "https://lnkd.in/p/g2X4FX6S")

    result = asyncio.run(go())
    assert result.url.startswith("https://www.linkedin.com/posts/")
    assert result.author == "Stanislav Beliaev"
    assert len(result.text) > 1000


# ---------- read_og_meta ----------

OG_HTML = """<html><head>
<title>Fallback title</title>
<META PROPERTY="og:title" CONTENT="Ben &amp; Jerry's post">
<meta property="og:description" content="The preview text.">
<meta property="og:description" content="A second, ignored description.">
<meta name="author" content="Ada">
<meta property="og:site_name" content="LinkedIn">
</head><body></body></html>"""


def test_og_meta_reads_tags():
    """Uppercase tags, &amp; entities and duplicate tags are all handled."""
    r = asyncio.run(read_og_meta(fake_client(body=OG_HTML), "https://example.com"))
    assert r.tool == "read_og_meta"
    assert r.title == "Ben & Jerry's post"
    assert r.text == "The preview text."  # the first description wins
    assert r.author == "Ada"
    assert r.extra == {"site": "LinkedIn"}


def test_og_meta_falls_back_to_title_and_plain_description():
    html = (
        "<html><head><title> Plain page </title>"
        '<meta name="description" content="Plain description."></head></html>'
    )
    r = asyncio.run(read_og_meta(fake_client(body=html), "https://example.com"))
    assert r.title == "Plain page"
    assert r.text == "Plain description."


def test_og_meta_without_any_description_fails():
    html = "<html><head><title>x</title></head></html>"
    with pytest.raises(ExtractionError, match="no og:description"):
        asyncio.run(read_og_meta(fake_client(body=html), "https://example.com"))


@pytest.mark.live
def test_live_linkedin_og_meta():
    async def go():
        async with make_client(
            timeout_seconds=settings.extraction.timeout_seconds
        ) as client:
            return await read_og_meta(client, "https://lnkd.in/p/g2X4FX6S")

    r = asyncio.run(go())
    assert r.extra["site"] == "LinkedIn"
    assert len(r.text) > 200
