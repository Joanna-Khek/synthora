import asyncio
import shutil

import pytest

from recorded_web import replay_client
from synthora.domain import ExtractionError
from synthora.extraction import (
    extract_article,
    extract_with_defuddle,
    read_github_repo,
    read_youtube_metadata,
)

LINKEDIN_URL = "https://lnkd.in/p/g2X4FX6S"
A_READER_COMMENT = (
    "You list them by stars not bu efficiency"  # from the comments under the post
)

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js not installed"
)


def run(case: str, extractor, *args):
    async def go():
        async with replay_client(case) as client:
            return await extractor(client, *args)

    return asyncio.run(go())


def test_article_stephango():
    r = run("article_stephango", extract_article, "https://stephango.com/saw")
    assert r.title == "Use the saw, fear the saw"
    assert r.author == "Steph Ango"
    assert "table saw" in r.text


def test_article_follows_redirects_to_the_final_url():
    r = run(
        "article_deeplearning",
        extract_article,
        "https://www.deeplearning.ai/short-courses/agent-skills-with-anthropic/",
    )
    assert r.url == "https://www.deeplearning.ai/courses/agent-skills-with-anthropic"
    assert r.title == "Agent Skills with Anthropic"


@needs_node
def test_linkedin_defuddle_keeps_the_post_and_drops_the_comments():
    """Spike S1, as a regression test: why LinkedIn uses Defuddle (PRD FR-8)."""
    r = run("linkedin_post", extract_with_defuddle, LINKEDIN_URL)
    assert r.author == "Stanislav Beliaev"
    assert r.url.startswith("https://www.linkedin.com/posts/stasbel_")
    assert "Best 12 GitHub repos" in r.text
    assert A_READER_COMMENT not in r.text


def test_linkedin_trafilatura_would_include_the_comments():
    """The other half of S1: if this ever stops being true, revisit the choice."""
    r = run("linkedin_post", extract_article, LINKEDIN_URL)
    assert A_READER_COMMENT in r.text


def test_github_repo():
    r = run(
        "github_obsidian_skills",
        read_github_repo,
        "https://github.com/obsidianmd/obsidian-skills",
    )
    assert r.title == "obsidianmd/obsidian-skills"
    assert "agents" in r.extra["topics"]
    assert "## README" in r.text and "## Files" in r.text


def test_youtube_video():
    r = run(
        "youtube_video",
        read_youtube_metadata,
        "https://youtu.be/dQw4w9WgXcQ",
        "ANY-KEY",
    )
    assert r.author == "Rick Astley"
    assert r.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_missing_video_is_a_failed_capture():
    with pytest.raises(ExtractionError, match="not found"):
        run(
            "youtube_missing",
            read_youtube_metadata,
            "https://youtu.be/AAAAAAAAAAA",
            "ANY-KEY",
        )


def test_an_unrecorded_request_fails_loudly():
    """If an extractor starts making a new request, the test says so instead of guessing."""
    with pytest.raises(AssertionError, match="not recorded"):
        run(
            "article_stephango",
            extract_article,
            "https://stephango.com/some-other-page",
        )
