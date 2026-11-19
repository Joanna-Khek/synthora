from urllib.parse import urlsplit
from synthora.server.schemas import Capture, SourceType


def _host_and_path(url: str) -> tuple[str, list[str]]:
    if "://" not in url:  # Telegram also detects "github.com/a/b" with no scheme
        url = "https://" + url
    parts = urlsplit(url)
    host = (
        (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    )  # .hostname is lowercase
    segments = [s for s in parts.path.split("/") if s]
    return host, segments


def _is_domain(host: str, domain: str) -> bool:
    """True for the domain itself or a subdomain of it"""
    return host == domain or host.endswith("." + domain)


def guess_source_type(url: str) -> SourceType:
    host, path = _host_and_path(url)
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
