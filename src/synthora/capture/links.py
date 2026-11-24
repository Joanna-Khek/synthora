"""For linkedin links, the real link is hidden inside LinkedIn's redirect URL. So we need to unwrap it"""

import re
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict

# Markdown links: [label](url) and images ![alt](url). The url may contain one level of (...).
MARKDOWN_LINK = re.compile(r"(!?)\[([^\]]*)\]\(((?:[^()\s]|\([^()]*\))*)\)")


class PostLink(BaseModel):
    """One link found in a post, with the number the LLM will refer to it by."""

    model_config = ConfigDict(frozen=True)

    id: int
    url: str
    label: str


def unwrap_linkedin_redirect(url: str) -> str:
    """linkedin.com/redir/redirect?url=<real link> -> <real link>. Other URLs are returned unchanged."""
    parts = urlsplit(url)
    if (
        parts.hostname
        and parts.hostname.endswith("linkedin.com")
        and parts.path.startswith("/redir/redirect")
    ):
        target = parse_qs(parts.query).get("url")
        if target:
            return target[0]
    return url


def number_links(text: str) -> tuple[str, list[PostLink]]:
    """Replace each Markdown link with 'label [link N]' and return the numbered links."""
    links: list[PostLink] = []
    ids_by_url: dict[str, int] = {}

    def replace(match: re.Match) -> str:
        is_image, label, url = match.groups()
        if is_image:
            return ""
        url = unwrap_linkedin_redirect(url)
        if url not in ids_by_url:
            ids_by_url[url] = len(links) + 1
            links.append(PostLink(id=ids_by_url[url], url=url, label=label))
        return f"{label} [link {ids_by_url[url]}]"

    return MARKDOWN_LINK.sub(replace, text), links
