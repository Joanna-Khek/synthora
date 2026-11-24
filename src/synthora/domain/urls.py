"""URL parsing shared by the Source Type rules, the agent and the extractors."""

from typing import NamedTuple
from urllib.parse import urlsplit


class UrlParts(NamedTuple):
    host: str  # lowercase, without a leading "www." or "m."
    segments: list[str]  # the non-empty path parts: /a//b/ -> ["a", "b"]
    query: str


def split_url(url: str) -> UrlParts:
    """Split a URL into the parts the rules match on.

    A URL with no scheme is read as https: Telegram also detects "github.com/a/b".
    """
    parts = urlsplit(url if "://" in url else "https://" + url)
    host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    segments = [s for s in parts.path.split("/") if s]
    return UrlParts(host, segments, parts.query)
