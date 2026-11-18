"""Tests for read_github_repo. A fake GitHub API answers by URL path."""

import asyncio

import httpx
import pytest

from synthora.server.extraction.fetch import ExtractionError, make_client
from synthora.server.extraction.github import parse_repo_url, read_github_repo

REPO_URL = "https://github.com/obsidianmd/obsidian-skills"
REPO_JSON = {
    "full_name": "obsidianmd/obsidian-skills",
    "html_url": REPO_URL,
    "description": "Agent skills for Obsidian",
    "default_branch": "main",
    "owner": {"login": "obsidianmd"},
    "topics": ["obsidian", "agents"],
    "stargazers_count": 123,
    "language": "Shell",
    "created_at": "2025-01-01T00:00:00Z",
}
TREE_JSON = {"tree": [{"path": "README.md"}, {"path": "skills/obsidian-cli/SKILL.md"}]}


def fake_github(auth_headers_seen: list | None = None) -> httpx.AsyncClient:
    """A tiny fake of the 3 GitHub endpoints we use; anything else is 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if auth_headers_seen is not None:
            auth_headers_seen.append(request.headers.get("authorization"))
        path = request.url.path
        if path == "/repos/obsidianmd/obsidian-skills":
            return httpx.Response(
                200, json=REPO_JSON, headers={"x-ratelimit-remaining": "4999"}
            )
        if path == "/repos/obsidianmd/obsidian-skills/readme":
            return httpx.Response(
                200, text="# Obsidian skills\nTeach your agent Obsidian."
            )
        if path == "/repos/obsidianmd/obsidian-skills/git/trees/main":
            return httpx.Response(200, json=TREE_JSON)
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(REPO_URL, id="plain"),
        pytest.param(REPO_URL + "/tree/main/skills", id="subfolder"),
        pytest.param(REPO_URL + ".git", id="clone-url"),
    ],
)
def test_parse_repo_url(url):
    assert parse_repo_url(url) == ("obsidianmd", "obsidian-skills")


def test_parse_repo_url_rejects_a_user_page():
    with pytest.raises(ExtractionError, match="not a repository"):
        parse_repo_url("https://github.com/obsidianmd")


def test_read_github_repo_combines_info_readme_and_tree():
    r = asyncio.run(read_github_repo(fake_github(), REPO_URL))
    assert r.tool == "read_github_repo"
    assert r.title == "obsidianmd/obsidian-skills"
    assert r.author == "obsidianmd"
    assert "Teach your agent Obsidian." in r.text
    assert "skills/obsidian-cli/SKILL.md" in r.text
    assert r.extra["topics"] == "obsidian, agents"
    assert r.extra["stars"] == "123"


def test_token_is_sent_only_when_given():
    seen: list = []
    asyncio.run(read_github_repo(fake_github(seen), REPO_URL, token="abc"))
    assert seen and all(h == "Bearer abc" for h in seen)

    seen.clear()
    asyncio.run(read_github_repo(fake_github(seen), REPO_URL))
    assert seen and all(h is None for h in seen)


def test_missing_repo_is_a_clean_error():
    with pytest.raises(ExtractionError, match="not found"):
        asyncio.run(
            read_github_repo(fake_github(), "https://github.com/nobody/nothing")
        )


def test_rate_limit_says_when_it_resets():
    def handler(request):
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError, match="resets at 22:13 UTC"):
        asyncio.run(read_github_repo(client, REPO_URL))


@pytest.mark.live
def test_live_github_repo():
    async def go():
        async with make_client() as client:
            return await read_github_repo(client, REPO_URL)

    r = asyncio.run(go())
    assert r.title == "obsidianmd/obsidian-skills"
    assert "## README" in r.text
