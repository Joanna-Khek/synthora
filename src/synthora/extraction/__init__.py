from .fetch import fetch_html, make_client
from .github import read_github_repo
from .html_extractors import extract_article, extract_with_defuddle, read_og_meta
from .youtube import read_youtube_metadata

__all__ = [
    "extract_article",
    "extract_with_defuddle",
    "fetch_html",
    "make_client",
    "read_github_repo",
    "read_og_meta",
    "read_youtube_metadata",
]
