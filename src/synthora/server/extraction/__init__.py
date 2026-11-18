from .fetch import ExtractionError, fetch_html, make_client
from .github import read_github_repo
from .html_extractors import extract_article, extract_with_defuddle, read_og_meta
from .youtube import read_youtube_metadata

__all__ = [
    "ExtractionError",
    "fetch_html",
    "make_client",
    "extract_article",
    "extract_with_defuddle",
    "read_og_meta",
    "read_github_repo",
    "read_youtube_metadata",
]
