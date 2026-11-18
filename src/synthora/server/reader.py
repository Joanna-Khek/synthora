import httpx

from synthora.schemas import ExtractedSource, SourceType
from synthora.server.extraction import (
    extract_article,
    extract_with_defuddle,
    read_github_repo,
    read_youtube_metadata,
)


async def read_url(
    client: httpx.AsyncClient,
    url: str,
    source_type: SourceType,
    *,
    youtube_key: str,
    github_token: str | None = None,
) -> ExtractedSource:
    """Call the one extractor for this Source Type. Raises ExtractionError on failure."""
    if source_type == SourceType.GITHUB_REPO:
        return await read_github_repo(client, url, token=github_token)
    if source_type == SourceType.YOUTUBE_VIDEO:
        return await read_youtube_metadata(client, url, youtube_key)
    if source_type == SourceType.LINKEDIN_POST:
        return await extract_with_defuddle(client, url)
    return await extract_article(client, url)
