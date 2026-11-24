import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

FIXTURES = Path(__file__).parent / "fixtures"

# Query parameters that are personal or tracking-only. They are removed from
# every saved URL, so fixtures never contain e.g. your LinkedIn share token.
PRIVATE_PARAMS = {
    "rcm",
    "si",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
}

# The only response headers worth keeping. Request headers are never saved,
# because that is where the API keys travel.
KEEP_HEADERS = {
    "content-type",
    "location",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}


def scrub(url: str) -> str:
    """Drop private query parameters, so a URL is safe to save and to match on."""
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in PRIVATE_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


class RecordingTransport(httpx.AsyncBaseTransport):
    """Sends requests to the real internet and keeps a copy of every response."""

    def __init__(self) -> None:
        self._real = httpx.AsyncHTTPTransport()
        self.entries: list[tuple[httpx.Request, int, dict[str, str], bytes]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._real.handle_async_request(request)
        body = await response.aread()
        headers = {
            k: scrub(v) if k == "location" else v
            for k, v in response.headers.items()
            if k.lower() in KEEP_HEADERS
        }
        self.entries.append((request, response.status_code, headers, body))
        # aread() already decompressed the body, so drop the headers that describe
        # the compressed form, or httpx would try to decompress it a second time.
        passthrough = {
            k: v
            for k, v in response.headers.items()
            if k.lower()
            not in ("content-encoding", "content-length", "transfer-encoding")
        }
        return httpx.Response(response.status_code, headers=passthrough, content=body)

    async def aclose(self) -> None:
        await self._real.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    """Answers requests from a recorded case folder. Unrecorded requests fail loudly."""

    def __init__(self, case: str) -> None:
        self._dir = FIXTURES / case
        manifest = json.loads((self._dir / "manifest.json").read_text(encoding="utf-8"))
        self._by_request = {(e["method"], e["url"]): e for e in manifest}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, scrub(str(request.url)))
        entry = self._by_request.get(key)
        if entry is None:
            raise AssertionError(f"not recorded in {self._dir.name}: {key}")
        body = (self._dir / entry["body"]).read_bytes()
        return httpx.Response(entry["status"], headers=entry["headers"], content=body)


def replay_client(case: str) -> httpx.AsyncClient:
    """A client like make_client(), but answered from tests/fixtures/<case>/."""
    return httpx.AsyncClient(transport=ReplayTransport(case), follow_redirects=True)


def _extension(content_type: str) -> str:
    if "json" in content_type:
        return ".json"
    if "html" in content_type:
        return ".html"
    return ".txt"


def save_case(case: str, recorder: RecordingTransport) -> None:
    folder = FIXTURES / case
    folder.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, (request, status, headers, body) in enumerate(recorder.entries):
        name = f"{i}{_extension(headers.get('content-type', ''))}"
        (folder / name).write_bytes(body)
        manifest.append(
            {
                "method": request.method,
                "url": scrub(str(request.url)),
                "status": status,
                "headers": headers,
                "body": name,
            }
        )
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"{case}: {len(manifest)} responses -> {folder}")


async def record_all() -> None:
    from synthora.config import Settings
    from synthora.domain import ExtractionError
    from synthora.extraction import (
        extract_article,
        extract_with_defuddle,
        read_github_repo,
        read_youtube_metadata,
    )
    from synthora.extraction.fetch import USER_AGENT

    settings = Settings()
    youtube_key = settings.google_api_key.get_secret_value()
    github_token = (
        settings.github_token.get_secret_value() if settings.github_token else None
    )

    cases = {
        "article_stephango": lambda c: extract_article(c, "https://stephango.com/saw"),
        "article_deeplearning": lambda c: extract_article(
            c, "https://www.deeplearning.ai/short-courses/agent-skills-with-anthropic/"
        ),
        "linkedin_post": lambda c: extract_with_defuddle(
            c, "https://lnkd.in/p/g2X4FX6S"
        ),
        "github_obsidian_skills": lambda c: read_github_repo(
            c, "https://github.com/obsidianmd/obsidian-skills", token=github_token
        ),
        "youtube_video": lambda c: read_youtube_metadata(
            c, "https://youtu.be/dQw4w9WgXcQ", youtube_key
        ),
        "youtube_missing": lambda c: read_youtube_metadata(
            c, "https://youtu.be/AAAAAAAAAAA", youtube_key
        ),
    }
    for case, run in cases.items():
        recorder = RecordingTransport()
        async with httpx.AsyncClient(
            transport=recorder,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        ) as client:
            try:
                await run(client)
            except ExtractionError as e:
                print(f"{case}: (expected?) {e}")
        save_case(case, recorder)


if __name__ == "__main__":
    asyncio.run(record_all())
