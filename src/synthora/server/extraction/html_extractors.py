import asyncio
import httpx
import json
import trafilatura

from synthora.schemas import ExtractedSource
from synthora.server.extraction.fetch import ExtractionError, fetch_html
from synthora.server.extraction.metadata_parser import _MetaTagParser
from synthora.config import ROOT

DEFUDDLE_CLI = ROOT / "node_modules" / "defuddle" / "dist" / "cli.js"
MIN_TEXT_CHARS = 200


def _trafilatura(html: str, url: str) -> ExtractedSource | None:
    text = trafilatura.extract(filecontent=html, url=url, output_format="markdown")
    if not text or len(text) < MIN_TEXT_CHARS:
        return None
    meta = trafilatura.extract_metadata(filecontent=html, default_url=url)
    return ExtractedSource(
        tool="extract_article",
        url=url,
        title=meta.title if meta else None,
        author=meta.author if meta else None,
        published=meta.date if meta else None,
        text=text,
    )


async def extract_article(client: httpx.AsyncClient, url: str) -> ExtractedSource:
    final_url, html = await fetch_html(client, url)
    # Runs the function in a worker thread since parsing big page can
    # take some CPU time.
    result = await asyncio.to_thread(_trafilatura, html, final_url)
    if result is None:
        raise ExtractionError(f"trafilatura found no main text at {final_url}")
    return result


async def extract_with_defuddle(client: httpx.AsyncClient, url: str) -> ExtractedSource:
    # We pipe the HTML in instaed of handing Defuddle the URL directly so that
    # every network request goes through our client
    final_url, html = await fetch_html(client, url)

    proc = await asyncio.create_subprocess_exec(
        "node",
        str(DEFUDDLE_CLI),
        "parse",
        "-",
        "--json",
        "--markdown",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        out, err = await asyncio.wait_for(
            proc.communicate(html.encode("utf-8")), timeout=30
        )
    except TimeoutError:
        proc.kill()
        raise ExtractionError("Defuddle took longer than 30s") from None

    if proc.returncode != 0:
        raise ExtractionError(f"Defuddle failed: {err.decode(errors='replace')}")

    data = json.loads(out)
    text = (data.get("content") or "").strip()

    if len(text) < MIN_TEXT_CHARS:
        raise ExtractionError(f"Defuddle found no main text at {final_url}")

    return ExtractedSource(
        tool="extract_with_defuddle",
        url=final_url,
        title=data.get("title") or None,
        author=data.get("author") or None,
        published=data.get("published") or None,
        text=text,
        extra={"word_count": str(data.get("wordCount", ""))},
    )


async def read_og_meta(client: httpx.AsyncClient, url: str) -> ExtractedSource:
    final_url, html = await fetch_html(client, url)
    parser = _MetaTagParser()
    parser.feed(html)
    m = parser.meta
    text = m.get("og:description") or m.get("description") or ""
    if not text:
        raise ExtractionError(f"no og:description or description at {final_url}")

    return ExtractedSource(
        tool="read_og_meta",
        url=final_url,
        title=m.get("og:title") or parser.title.strip() or None,
        author=m.get("author") or m.get("article:author"),
        published=m.get("article:published_time"),
        text=text,
        extra={"site": m.get("og:site_name", "")},
    )
