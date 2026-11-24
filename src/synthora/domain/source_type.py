from enum import StrEnum

from synthora.domain.capture import Capture
from synthora.domain.urls import split_url


class SourceType(StrEnum):
    """The Source of the links"""

    WEB_ARTICLE = "web_article"
    GITHUB_REPO = "github_repo"
    IMAGE = "image"
    LINKEDIN_POST = "linkedin_post"
    YOUTUBE_VIDEO = "youtube_video"


def _is_domain(host: str, domain: str) -> bool:
    """True for the domain itself or a subdomain of it"""
    return host == domain or host.endswith("." + domain)


def guess_source_type(url: str) -> SourceType:
    host, path, _ = split_url(url)
    if host == "github.com" and len(path) >= 2:  # /owner/repo[/...]
        return SourceType.GITHUB_REPO
    if _is_domain(host, "linkedin.com") and path[:1] in (["posts"], ["feed"]):
        return SourceType.LINKEDIN_POST
    if host == "lnkd.in" and path[:1] == [
        "p"
    ]:  # LinkedIn's short link for a shared post
        return SourceType.LINKEDIN_POST
    if host == "youtu.be" and path:
        return SourceType.YOUTUBE_VIDEO
    if _is_domain(host, "youtube.com") and path[:1] in (
        ["watch"],
        ["shorts"],
        ["live"],
    ):
        return SourceType.YOUTUBE_VIDEO
    return SourceType.WEB_ARTICLE  # safe default


def hint_for(capture: Capture) -> SourceType:
    return (
        SourceType.IMAGE
        if capture.kind == "image"
        else guess_source_type(capture.value)
    )
