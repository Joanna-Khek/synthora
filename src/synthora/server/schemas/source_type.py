from enum import StrEnum


class SourceType(StrEnum):
    """The Source of the links"""

    WEB_ARTICLE = "web_article"
    GITHUB_REPO = "github_repo"
    IMAGE = "image"
    LINKEDIN_POST = "linkedin_post"
    YOUTUBE_VIDEO = "youtube_video"
