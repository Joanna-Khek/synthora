import httpx

from synthora.domain import ExtractionError
from synthora.extraction.ssrf import BlockedURLError, check_url

USER_AGENT = "Mozilla/5.0 (compatible; synthora/0.1)"
MAX_HTML_BYTES = 5_000_000  # refuse pages bigger than 5 MB


async def _ssrf_guard(request: httpx.Request) -> None:
    """Runs before EVERY request the client sends, including each redirect hop."""
    try:
        await check_url(str(request.url))
    except BlockedURLError as e:
        raise ExtractionError(str(e)) from e


def make_client(
    timeout_seconds: float, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The one HTTP client for all fetching. `transport` lets tests fake the web."""
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=timeout_seconds,
        transport=transport,
        event_hooks={
            "request": [_ssrf_guard]
        },  # calls ssrf guard before sending each request
    )


async def fetch_html(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """Download a web apge. Returns (final_url, html)"""
    try:
        resp = await client.get(url)
    except httpx.HTTPError as e:
        raise ExtractionError(f"Could not fetch {url}: {type(e).__name__}") from e

    # Check for status 200
    if resp.status_code != 200:
        raise ExtractionError(f"{url} answered HTTP {resp.status_code}")

    # Check response is HTML
    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type:
        raise ExtractionError(f"{url} is not a web page ({content_type or 'no type'})")

    # Check response is not large
    if len(resp.content) > MAX_HTML_BYTES:
        raise ExtractionError(f"{url} is larger than {MAX_HTML_BYTES} bytes")

    return str(resp.url), resp.text
