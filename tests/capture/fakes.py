"""Fake Tools and a sample LinkedIn post, shared by the graph and pipeline tests.

No network, no MCP server, no API calls: every outside dependency is faked
by subclassing Tools and overriding only the methods a test needs.
"""

from synthora.capture.distiller import DistillError
from synthora.capture.links import PostLink, number_links
from synthora.capture.tools import Tools
from synthora.domain import Distillation, ExtractedSource, ExtractionError, SourceType


def redirect(short: str) -> str:
    """How Defuddle shows a link in a LinkedIn post: wrapped in LinkedIn's redirect URL."""
    encoded = short.replace("://", "%3A%2F%2F").replace("/", "%2F").replace(".", "%2E")
    return f"https://www.linkedin.com/redir/redirect?url={encoded}&urlhash=abc&trk=public_post-text"


# A shortened copy of the real "Best 12 GitHub repos" post, in Defuddle's Markdown format:
# two resource links, the first one repeated, then the author's self-promotion.
POST_TEXT = (
    "Best GitHub repos to secure your AI-generated code 👇 "
    f"(1) Strix: 🔗 [https://lnkd.in/gThZcnms]({redirect('https://lnkd.in/gThZcnms')}) "
    "AI hacker that attacks your live app. "
    f"(2) Trivy: 🔗 [https://lnkd.in/g9q7854R]({redirect('https://lnkd.in/g9q7854R')}) "
    "Catches vulnerable packages. "
    f"Strix again: [https://lnkd.in/gThZcnms]({redirect('https://lnkd.in/gThZcnms')}) "
    "![a picture](https://media.licdn.com/pic.png) "
    f"-- P.S. Try my English tutor → [GetFluently.app]({redirect('http://GetFluently.app')})"
)

POST_URL = "https://lnkd.in/p/g2X4FX6S"
ARTICLE_URL = "https://stephango.com/saw"

POST = ExtractedSource(
    tool="extract_with_defuddle",
    url=POST_URL,
    title="Best GitHub repos",
    text=POST_TEXT,
)


# ---------------------------------------------------------------- fake tools
class FakeTools(Tools):
    """Every method works; a test subclasses this and breaks only what it needs."""

    def __init__(self, picks: list[int] | None = None) -> None:
        self.picks = [1, 2] if picks is None else picks  # what the "LLM" picks
        self.calls: list[str] = []  # records which methods ran, in order, for spying

    async def read(self, url: str, source_type: SourceType) -> ExtractedSource:
        self.calls.append(f"read {url}")
        if source_type is SourceType.LINKEDIN_POST:
            return POST
        return ExtractedSource(
            tool="fake", url=url, title=f"Title of {url}", text="Some text."
        )

    async def distill(self, source: ExtractedSource) -> Distillation:
        self.calls.append(f"distill {source.url}")
        return Distillation(
            tldr=f"TL;DR of {source.url}", key_ideas=["An idea."], notable_quotes=[]
        )

    async def resolve(self, url: str) -> str:
        self.calls.append(f"resolve {url}")
        return "https://github.com/example/" + url.rsplit("/", 1)[1]

    async def pick_links(
        self, post: ExtractedSource
    ) -> tuple[list[PostLink], list[int]]:
        self.calls.append("pick_links")
        _, links = number_links(post.text)
        return links, self.picks


class BrokenReadTools(FakeTools):
    async def read(self, url, source_type):
        raise ExtractionError("HTTP 404")


class BrokenDistillTools(FakeTools):
    async def distill(self, source):
        raise DistillError("Claude declined to distill this source")
