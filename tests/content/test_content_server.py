import asyncio

import pytest
from mcp.client import Client

from synthora.content import mcp_server
from synthora.obsidian.vault import ObsidianError

RAG = "Concepts/Retrieval-Augmented Generation.md"


class FakeVault:
    """Just enough of Vault for the read tools, backed by a dict."""

    def __init__(self) -> None:
        self.notes = {
            RAG: "---\ntype: concept\naliases:\n  - RAG\n---\n# RAG body",
            "Sources/Some post.md": "uses [[Retrieval-Augmented Generation]]",
        }

    def exists(self, path: str) -> bool:
        return path in self.notes

    def aliases(self) -> dict[str, str]:
        return {"RAG": RAG}

    def read(self, path: str) -> str:
        if path not in self.notes:
            raise ObsidianError(f"File {path} not found.")
        return self.notes[path]

    def backlinks(self, path: str) -> list[str]:
        return ["Sources/Some post.md"] if path == RAG else []

    def search(self, query: str, *, folder=None, limit=None) -> list[str]:
        hits = [p for p, body in self.notes.items() if query.lower() in body.lower()]
        return hits[:limit]

    def list_notes(self, folder: str) -> list[str]:
        return sorted(p for p in self.notes if p.startswith(folder + "/"))


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(mcp_server, "open_vault", lambda: fake)
    return fake


def call(tool: str, arguments: dict | None = None):
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(tool, arguments or {})

    return asyncio.run(go())


def test_lists_all_tools():
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.list_tools()

    names = {t.name for t in asyncio.run(go()).tools}
    assert names == {
        "find_node",
        "search_vault",
        "read_note",
        "list_backlinks",
        "relation_vocabulary",
        "create_node",
        "update_synthesis",
        "append_evidence",
        "add_alias",
        "add_relation",
    }


@pytest.mark.parametrize(
    ("name", "matched_by"),
    [
        ("Retrieval-Augmented Generation", "title"),
        ("retrieval augmented generation", "title"),
        ("RAG", "alias"),
        ("rag", "alias"),
        ("RAGs", "alias"),
    ],
)
def test_find_node_by_title_or_alias(name, matched_by):
    result = call("find_node", {"name": name})
    assert result.structured_content["match"] == {"path": RAG, "matched_by": matched_by}
    # this fake note has no sections (like a hand-written one): the lookup still works
    assert result.structured_content["synthesis"] is None


def test_find_node_returns_null_when_nothing_matches():
    result = call("find_node", {"name": "Vector Database"})
    assert not result.is_error
    assert result.structured_content == {
        "match": None,
        "similar": [],
        "synthesis": None,
        "evidence": [],
    }


def test_find_node_suggests_nodes_that_share_a_word():
    result = call("find_node", {"name": "Generation Strategies"})
    assert result.structured_content == {
        "match": None,
        "similar": [RAG],
        "synthesis": None,
        "evidence": [],
    }


@pytest.mark.parametrize(
    "path",
    [
        "../secrets.md",
        "Concepts/../../x.md",
        "Concepts\\..\\x.md",
        "Concepts/x.txt",
        ".obsidian/app.json",
        "Attachments/a.md",
    ],
)
def test_read_note_refuses_paths_outside_note_folders(path):
    result = call("read_note", {"path": path})
    assert result.is_error
    assert "not a note path" in result.content[0].text


def test_read_note_missing_is_a_readable_error():
    result = call("read_note", {"path": "Concepts/Nope.md"})
    assert result.is_error
    assert "not found" in result.content[0].text


def test_search_limit_is_checked_by_the_schema():
    assert call("search_vault", {"query": "rag", "limit": 500}).is_error
    assert call("search_vault", {"query": "  "}).is_error
    assert call("search_vault", {"query": "rag"}).structured_content["result"] == [RAG]


def test_backlinks_and_vocabulary():
    assert call("list_backlinks", {"path": RAG}).structured_content["result"] == [
        "Sources/Some post.md"
    ]
    assert "uses" in call("relation_vocabulary").structured_content["result"]
