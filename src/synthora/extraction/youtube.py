from urllib.parse import parse_qs

import httpx

from synthora.domain import ExtractedSource, ExtractionError
from synthora.domain.urls import split_url

VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"


def video_id_from_url(url: str) -> str:
    """Handles youtu.be/<id>, youtube.com/watch?v=<id>, /shorts/<id> and /live/<id>."""
    host, path, query = split_url(url)
    if host == "youtu.be" and path:
        return path[0]
    if path[:1] == ["watch"]:
        ids = parse_qs(query).get("v")
        if ids:
            return ids[0]
    if path[:1] in (["shorts"], ["live"]) and len(path) >= 2:
        return path[1]
    raise ExtractionError(f"no video id in {url}")


async def read_youtube_metadata(
    client: httpx.AsyncClient, url: str, api_key: str
) -> ExtractedSource:
    video_id = video_id_from_url(url)
    resp = await client.get(
        VIDEOS_ENDPOINT,
        params={"part": "snippet", "id": video_id},
        headers={"X-Goog-Api-Key": api_key},
    )

    if resp.status_code == 403:
        # Each videos.list call costs 1 unit; the default quota is 10,000/day.
        reason = (
            resp.json()
            .get("error", {})
            .get("errors", [{}])[0]
            .get("reason", "forbidden")
        )
        raise ExtractionError(f"YouTube API refused the request: {reason}")

    if resp.status_code != 200:
        raise ExtractionError(f"YouTube API answered HTTP {resp.status_code}")

    items = resp.json().get("items", [])
    if not items:
        raise ExtractionError(f"video {video_id} not found (deleted or private?)")

    s = items[0]["snippet"]
    return ExtractedSource(
        tool="read_youtube_metadata",
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=s.get("title"),
        author=s.get("channelTitle"),
        published=s.get("publishedAt"),
        text=s.get("description") or "(no description)",
        extra={"tags": ", ".join(s.get("tags", [])), "video_id": video_id},
    )
