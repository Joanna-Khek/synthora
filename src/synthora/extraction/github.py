import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from synthora.domain import ExtractedSource, ExtractionError

API = "https://api.github.com"
MAX_TREE_ENTRIES = 150  # enough to see the shape of a repo, not every file
MAX_README_CHARS = 30_000


def parse_repo_url(url: str) -> tuple[str, str]:
    """For example:
    Input: 'https://github.com/obsidianmd/obsidian-skills/tree/main'
    Output: ('obsidianmd', 'obsidian-skills')
    """
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) < 2:
        raise ExtractionError(f"not a repository URL: {url}")
    return parts[0], parts[1].removesuffix(".git")


async def _get(
    client: httpx.AsyncClient, path: str, headers: dict[str, str]
) -> httpx.Response:
    resp = await client.get(API + path, headers=headers)
    # GitHub reports how many requests you have left in these headers.
    if (
        resp.status_code in (403, 429)
        and resp.headers.get("x-ratelimit-remaining") == "0"
    ):
        reset = int(resp.headers.get("x-ratelimit-reset", "0"))
        when = datetime.fromtimestamp(reset, tz=timezone.utc).strftime("%H:%M UTC")
        raise ExtractionError(f"GitHub rate limit reached; resets at {when}")
    return resp


async def read_github_repo(
    client: httpx.AsyncClient, url: str, token: str | None = None
) -> ExtractedSource:
    owner, repo = parse_repo_url(url)
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # 1. Repo info first: we need default_branch for the tree call
    resp = await _get(client, f"/repos/{owner}/{repo}", headers)
    if resp.status_code == 404:
        raise ExtractionError(f"repo {owner}/{repo} not found (or private)")
    resp.raise_for_status()
    info = resp.json()

    # 2. README and tree don't depend on each other, so we fetch them concurrently
    raw = {
        **headers,
        "Accept": "application/vnd.github.raw+json",
    }  # README as plain text
    readme_resp, tree_resp = await asyncio.gather(
        _get(client, f"/repos/{owner}/{repo}/readme", raw),
        _get(
            client,
            f"/repos/{owner}/{repo}/git/trees/{info['default_branch']}?recursive=1",
            headers,
        ),
    )
    readme = (
        readme_resp.text[:MAX_README_CHARS] if readme_resp.status_code == 200 else ""
    )
    paths = (
        [t["path"] for t in tree_resp.json().get("tree", [])]
        if tree_resp.status_code == 200
        else []
    )
    shown = paths[:MAX_TREE_ENTRIES]
    if len(paths) > len(shown):
        shown.append(f"... and {len(paths) - len(shown)} more")

    text = "\n\n".join(
        [
            f"# {info['full_name']}",
            info.get("description") or "(no description)",
            "## README",
            readme or "(no README)",
            "## Files",
            "\n".join(shown) or "(empty)",
        ]
    )

    return ExtractedSource(
        tool="read_github_repo",
        url=info["html_url"],
        title=info["full_name"],
        author=info["owner"]["login"],
        published=info.get("created_at"),
        text=text,
        extra={
            "description": info.get("description") or "",
            "topics": ", ".join(info.get("topics", [])),
            "stars": str(info.get("stargazers_count", 0)),
            "language": info.get("language") or "",
            "rate_limit_remaining": resp.headers.get("x-ratelimit-remaining", "?"),
        },
    )
