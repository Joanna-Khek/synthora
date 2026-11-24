"""Tests for the Source Type hint (PRD FR-4): one row per case."""

import pytest

from synthora.domain import SourceType, guess_source_type

GITHUB = "https://github.com/obsidianmd/obsidian-skills"
DLAI = "https://www.deeplearning.ai/courses/tabular-foundation-models-for-data-science-agents?utm_medium=social&utm_source=dlai-homepage"
LINKEDIN = "https://www.linkedin.com/posts/stasbel_best-12-github-repos-to-secure-your-ai-generated-share-7527015621663035392-npoh/?utm_source=share&utm_medium=member_ios&rcm=ACoAABX1_4oB8ZjceS24nXUbVDwSW-dL-pTzdro"

CASES = [
    # your real Captures
    pytest.param(GITHUB, SourceType.GITHUB_REPO, id="github-repo"),
    pytest.param(DLAI, SourceType.WEB_ARTICLE, id="dlai-course"),
    pytest.param(LINKEDIN, SourceType.LINKEDIN_POST, id="linkedin-post"),
    # GitHub
    pytest.param(
        "https://github.com/obsidianmd/obsidian-skills/tree/main/plugins",
        SourceType.GITHUB_REPO,
        id="github-subpage",
    ),
    pytest.param(
        "https://GitHub.com/obsidianmd/obsidian-skills",
        SourceType.GITHUB_REPO,
        id="github-uppercase",
    ),
    pytest.param(
        "github.com/obsidianmd/obsidian-skills",
        SourceType.GITHUB_REPO,
        id="github-no-scheme",
    ),
    pytest.param(
        "https://github.com/obsidianmd", SourceType.WEB_ARTICLE, id="github-user-page"
    ),
    # lookalikes must NOT match
    pytest.param(
        "https://github.com.evil.example/a/b",
        SourceType.WEB_ARTICLE,
        id="lookalike-domain",
    ),
    pytest.param(
        "https://notgithub.com/a/b", SourceType.WEB_ARTICLE, id="suffix-domain"
    ),
    pytest.param(
        "https://example.com/?ref=github.com/a/b",
        SourceType.WEB_ARTICLE,
        id="domain-in-query",
    ),
    # LinkedIn
    pytest.param(
        "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        SourceType.LINKEDIN_POST,
        id="linkedin-feed-update",
    ),
    pytest.param(
        "https://www.linkedin.com/in/someone/",
        SourceType.WEB_ARTICLE,
        id="linkedin-profile",
    ),
    # YouTube
    pytest.param(
        "https://www.youtube.com/watch?v=abc123XYZ_0",
        SourceType.YOUTUBE_VIDEO,
        id="youtube-watch",
    ),
    pytest.param(
        "https://youtu.be/abc123XYZ_0?si=xyz",
        SourceType.YOUTUBE_VIDEO,
        id="youtube-short-link",
    ),
    pytest.param(
        "https://m.youtube.com/watch?v=abc123XYZ_0",
        SourceType.YOUTUBE_VIDEO,
        id="youtube-mobile",
    ),
    pytest.param(
        "https://www.youtube.com/shorts/abc123XYZ_0",
        SourceType.YOUTUBE_VIDEO,
        id="youtube-shorts",
    ),
    pytest.param(
        "https://www.youtube.com/@somechannel",
        SourceType.WEB_ARTICLE,
        id="youtube-channel",
    ),
]


# Turns one function into many tests, one for each row in CASES.
@pytest.mark.parametrize("url, expected", CASES)
def test_guess_source_type(url: str, expected: SourceType) -> None:
    assert guess_source_type(url) == expected
