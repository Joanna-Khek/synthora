from pydantic import BaseModel, ConfigDict


class ExtractedSource(BaseModel):
    """What every extractor returns: clean text + metadata."""

    model_config = ConfigDict(frozen=True)

    tool: str  # which extractor produced this
    url: str  # the FINAL url, after redirects
    title: str | None = None
    author: str | None = None
    published: str | None = None
    text: str  # the main content as Markdown
    extra: dict[str, str] = {}  # for tool-specific facts
