from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from typing import cast

import httpx
import lxml.html
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

from synthora.config import Settings
from synthora.domain import ExtractedSource, ExtractionError
from synthora.extraction import github, html_extractors, youtube
from synthora.extraction.fetch import fetch_html, make_client


class Deps(BaseModel):
    """Things every tool needs, created once when the server starts."""

    # arbitrary-types_allowed=True because we are using httpx.AsyncClient and
    # pydantic validates fields like str, int, list.
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    http: httpx.AsyncClient
    youtube_key: str
    github_token: str | None


# Lifespan run once when the server starts.
@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncGenerator[Deps]:
    settings = Settings()
    async with make_client(timeout_seconds=settings.extraction.timeout_seconds) as http:
        yield Deps(
            http=http,
            youtube_key=settings.google_api_key.get_secret_value(),
            github_token=(
                settings.github_token.get_secret_value()
                if settings.github_token
                else None
            ),
        )


mcp = MCPServer(
    name="synthora-extraction",
    instructions="Read-only tools that turn a URL into clean text plus metadata.",
    lifespan=lifespan,
)


def _deps(ctx: Context) -> Deps:
    # The SDK types lifespan_context as a dict by default; we know our lifespan
    # yields a Deps, so tell the type checker with cast() (no runtime effect).
    return cast(Deps, ctx.request_context.lifespan_context)


async def _run(extraction: Awaitable[ExtractedSource]) -> ExtractedSource:
    """Turn out expected failures into ToolError, so the caller sees the reason."""
    try:
        return await extraction
    except ExtractionError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
async def extract_article(url: str, ctx: Context) -> ExtractedSource:
    """Extract the main text of an ordinary web page (article, blog post, docs page) using trafilatura"""
    return await _run(html_extractors.extract_article(client=_deps(ctx).http, url=url))


@mcp.tool()
async def extract_with_defuddle(url: str, ctx: Context) -> ExtractedSource:
    """Extract a LinkedIn post's own text (without the comments) using Defuddle."""
    return await _run(
        html_extractors.extract_with_defuddle(client=_deps(ctx).http, url=url)
    )


@mcp.tool()
async def read_og_meta(url: str, ctx: Context) -> ExtractedSource:
    """Read only a page's og: preview tags (title and short description)"""
    return await _run(html_extractors.read_og_meta(client=_deps(ctx).http, url=url))


@mcp.tool()
async def read_github_repo(url: str, ctx: Context) -> ExtractedSource:
    """Read a Github repository's description, topics, README and file list (no source code)."""
    deps = _deps(ctx)
    return await _run(
        github.read_github_repo(client=deps.http, url=url, token=deps.github_token)
    )


@mcp.tool()
async def read_youtube_metadata(url: str, ctx: Context) -> ExtractedSource:
    """Read a YouTube video's title, channel and description (metadata only, no transcript)."""
    deps = _deps(ctx)
    return await _run(youtube.read_youtube_metadata(deps.http, url, deps.youtube_key))


@mcp.tool()
async def resolve_link(url: str, ctx: Context) -> str:
    """Return the real destination of a LinkedIn lnkd.in short link."""

    async def resolve() -> str:
        _, html = await fetch_html(_deps(ctx).http, url)
        hrefs = lxml.html.fromstring(html).xpath(
            '//a[@data-tracking-control-name="external_url_click"]/@href'
        )
        if not hrefs:
            raise ExtractionError("no external link found on the lnkd.in page")
        return hrefs[0]

    try:
        return await resolve()
    except ExtractionError as e:
        raise ToolError(str(e)) from e


if __name__ == "__main__":
    mcp.run(transport="stdio")
