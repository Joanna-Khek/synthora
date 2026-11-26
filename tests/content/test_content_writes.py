"""Write tools: the Done-when of 10.0 is 'a tool refusing to touch My Notes'."""

import asyncio

import pytest
from mcp.client import Client

from synthora.content import mcp_server
from synthora.obsidian.notes import NodeNote
from synthora.obsidian.sections import check_my_notes_untouched, my_notes, section
from synthora.obsidian.notes import split_frontmatter

RAG = "Concepts/Retrieval-Augmented Generation.md"
MINE = "## My Notes\nMy own thoughts. Do not touch.\n"


class FakeVault:
    """A dict of notes. edit() runs the same My Notes check as the real Vault.edit."""

    def __init__(self) -> None:
        node = NodeNote(
            title="Retrieval-Augmented Generation",
            type="concept",
            synthesis="Old synthesis.",
            aliases=["RAG"],
        ).render()
        self.notes = {RAG: node.replace("## My Notes\n", MINE)}
        self.alias_map = {"RAG": RAG}  # alias -> note, like Obsidian's index

    def exists(self, path: str) -> bool:
        return path in self.notes

    def aliases(self) -> dict[str, str]:
        return dict(self.alias_map)

    def set_property(self, path: str, name: str, value) -> None:
        assert name == "aliases"
        self.alias_map = {a: p for a, p in self.alias_map.items() if p != path}
        self.alias_map |= {a: path for a in value}

    def create(self, path: str, body: str) -> None:
        if path in self.notes:
            raise FileExistsError(path)
        self.notes[path] = body

    def read(self, path: str) -> str:
        return self.notes[path]

    def edit(self, path: str, change) -> None:
        before = self.notes[path]
        after = change(before)
        check_my_notes_untouched(before, after)
        self.notes[path] = after

    def list_notes(self, folder: str) -> list[str]:
        return sorted(p for p in self.notes if p.startswith(folder + "/"))


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(mcp_server, "open_vault", lambda: fake)
    return fake


def call(tool: str, arguments: dict):
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(go())


# ---------------------------------------------------------------- My Notes (10.0 Done when)
def test_synthesis_trying_to_add_a_my_notes_heading_is_refused(vault):
    before = vault.notes[RAG]
    result = call(
        "update_synthesis", {"path": RAG, "synthesis": "New.\n## My Notes\nhijacked"}
    )
    assert result.is_error
    assert "## headings" in result.content[0].text
    assert vault.notes[RAG] == before  # nothing was written


def test_note_without_my_notes_heading_is_refused(vault):
    vault.notes[RAG] = vault.notes[RAG].replace(
        MINE, ""
    )  # the user deleted the heading
    before = vault.notes[RAG]
    result = call(
        "append_evidence", {"path": RAG, "source": "Some post", "text": "RAG helps."}
    )
    assert result.is_error
    assert "My Notes belongs to the user" in result.content[0].text
    assert vault.notes[RAG] == before


def test_allowed_edits_leave_my_notes_byte_identical(vault):
    assert not call(
        "update_synthesis", {"path": RAG, "synthesis": "New synthesis."}
    ).is_error
    assert not call(
        "append_evidence", {"path": RAG, "source": "Some post", "text": "RAG helps."}
    ).is_error
    text = vault.notes[RAG]
    assert section(text, "## Synthesis") == "New synthesis."
    assert "[[Some post]]: RAG helps." in section(text, "## Evidence")
    assert my_notes(text) == MINE


# ---------------------------------------------------------------- create_node
def test_create_node_writes_a_rendered_note(vault):
    result = call(
        "create_node",
        {
            "title": "Vector Database",
            "type": "concept",
            "synthesis": "Stores embeddings.",
            "aliases": ["Vector DB"],
            "relations": {"part_of": ["RAG"]},
        },
    )
    assert not result.is_error
    path = "Concepts/Vector Database.md"
    assert result.structured_content["result"] == path
    assert "## My Notes" in vault.notes[path]
    # FR-14: the relation was given as an alias, but links to the canonical title
    assert "- '[[Retrieval-Augmented Generation]]'" in vault.notes[path]


@pytest.mark.parametrize(
    "args",
    [
        {"title": "Retrieval-Augmented Generation"},  # same title
        {"title": "Retrieval Augmented Gen", "aliases": ["rag"]},  # alias collides
        {"title": "RAG"},  # title is someone's alias
    ],
)
def test_create_node_refuses_duplicates(vault, args):
    result = call("create_node", {"type": "concept", "synthesis": "x", **args})
    assert result.is_error
    assert "link to it instead" in result.content[0].text


def test_create_node_rejects_an_unknown_relation(vault):
    result = call(
        "create_node",
        {
            "title": "Vector Database",
            "type": "concept",
            "synthesis": "x",
            "relations": {"is_friends_with": ["Embedding"]},
        },
    )
    assert result.is_error  # the schema only allows the FR-16 vocabulary


def test_edit_tools_only_touch_nodes(vault):
    result = call(
        "update_synthesis", {"path": "Sources/Some post.md", "synthesis": "x"}
    )
    assert result.is_error
    assert "only Nodes can be edited" in result.content[0].text


def test_evidence_from_the_same_source_is_added_only_once(vault):
    args = {"path": RAG, "source": "Some post", "text": "RAG helps."}
    assert not call("append_evidence", args).is_error
    before = vault.notes[RAG]
    again = call("append_evidence", {**args, "text": "Said differently."})
    assert again.is_error
    assert "already has evidence from [[Some post]]" in again.content[0].text
    assert vault.notes[RAG] == before  # a retry changes nothing


@pytest.mark.parametrize(
    "title", ["Retrieval Augmented Generation", "retrieval-augmented generations"]
)
def test_create_node_refuses_a_near_duplicate_title(vault, title):
    result = call("create_node", {"title": title, "type": "concept", "synthesis": "x"})
    assert result.is_error
    assert "link to it instead" in result.content[0].text


# ---------------------------------------------------------------- add_alias
def test_add_alias_makes_the_next_lookup_an_exact_match(vault):
    assert (
        call("find_node", {"name": "Retrieval Generation"}).structured_content["match"]
        is None
    )
    assert not call(
        "add_alias", {"path": RAG, "alias": "Retrieval Generation"}
    ).is_error
    match = call("find_node", {"name": "retrieval generation"}).structured_content[
        "match"
    ]
    assert match == {"path": RAG, "matched_by": "alias"}
    assert set(vault.alias_map) == {
        "RAG",
        "Retrieval Generation",
    }  # the old alias is kept


def test_add_alias_already_there_changes_nothing(vault):
    result = call("add_alias", {"path": RAG, "alias": "rag"})
    assert not result.is_error and "nothing to add" in result.content[0].text
    assert vault.alias_map == {"RAG": RAG}


def test_add_alias_refuses_a_name_that_belongs_to_another_node(vault):
    call(
        "create_node", {"title": "Vector Database", "type": "concept", "synthesis": "x"}
    )
    result = call("add_alias", {"path": "Concepts/Vector Database.md", "alias": "RAG"})
    assert result.is_error and "already belongs to" in result.content[0].text


@pytest.mark.parametrize("alias", ["", "   ", "[[RAG]]", "x" * 101])
def test_add_alias_rejects_names_that_are_not_plain(vault, alias):
    assert call("add_alias", {"path": RAG, "alias": alias}).is_error


def test_add_alias_to_a_missing_note_is_refused(vault):
    result = call("add_alias", {"path": "Concepts/Nope.md", "alias": "Nope"})
    assert result.is_error and "doesn't exist" in result.content[0].text


# ---------------------------------------------------------------- relations (10.3)
def test_create_node_refuses_a_relation_to_a_node_that_does_not_exist(vault):
    result = call(
        "create_node",
        {
            "title": "Vector Database",
            "type": "concept",
            "synthesis": "x",
            "relations": {"uses": ["Embeddings Typo"]},
        },
    )
    assert result.is_error and "isn't a Node yet" in result.content[0].text
    assert "Concepts/Vector Database.md" not in vault.notes


def test_add_relation_links_two_existing_nodes_by_canonical_title(vault):
    call(
        "create_node", {"title": "Vector Database", "type": "concept", "synthesis": "x"}
    )
    before = my_notes(vault.notes[RAG])
    result = call(
        "add_relation", {"path": RAG, "relation": "uses", "target": "vector databases"}
    )
    assert not result.is_error
    props, _ = split_frontmatter(vault.notes[RAG])
    assert props["uses"] == ["[[Vector Database]]"]
    assert props["aliases"] == ["RAG"]  # other properties survive the round trip
    assert my_notes(vault.notes[RAG]) == before


def test_add_relation_twice_changes_nothing(vault):
    call(
        "create_node", {"title": "Vector Database", "type": "concept", "synthesis": "x"}
    )
    args = {"path": RAG, "relation": "uses", "target": "Vector Database"}
    call("add_relation", args)
    before = vault.notes[RAG]
    result = call("add_relation", args)
    assert not result.is_error and "nothing to add" in result.content[0].text
    assert vault.notes[RAG] == before


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"relation": "uses", "target": "Nope"}, "isn't a Node yet"),
        ({"relation": "uses", "target": "RAG"}, "related to itself"),
        (
            {"relation": "likes", "target": "RAG"},
            "",
        ),  # not in the vocabulary: schema error
    ],
)
def test_add_relation_refusals(vault, args, message):
    result = call("add_relation", {"path": RAG, **args})
    assert result.is_error and message in result.content[0].text


# ---------------------------------------------------------------- links in agent-written text (FR-14)
@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("Builds on [[RAG]].", "Builds on [[Retrieval-Augmented Generation|RAG]]."),
        (
            "Builds on [[retrieval augmented generation]].",
            "Builds on [[Retrieval-Augmented Generation|retrieval augmented generation]].",
        ),
        (
            "See [[Retrieval-Augmented Generation]].",
            "See [[Retrieval-Augmented Generation]].",
        ),
        (
            "See [[RAG|the RAG pattern]].",
            "See [[Retrieval-Augmented Generation|the RAG pattern]].",
        ),
        (
            "See [[RAG#Synthesis]].",
            "See [[Retrieval-Augmented Generation#Synthesis|RAG]].",
        ),
        (
            "No links at all, **bold** and ==highlight== are fine.",
            "No links at all, **bold** and ==highlight== are fine.",
        ),
    ],
)
def test_links_in_a_synthesis_point_at_canonical_titles(vault, written, stored):
    assert not call("update_synthesis", {"path": RAG, "synthesis": written}).is_error
    assert section(vault.notes[RAG], "## Synthesis") == stored


@pytest.mark.parametrize(
    ("written", "message"),
    [
        ("Uses [[Nonexistent Thing]].", "isn't a Node"),
        ("Diagram: ![[RAG]]", "embeds"),
        ("See [[#Evidence]].", "same-note links"),
    ],
)
def test_bad_links_are_refused_and_nothing_is_written(vault, written, message):
    before = vault.notes[RAG]
    result = call(
        "append_evidence", {"path": RAG, "source": "Some post", "text": written}
    )
    assert result.is_error and message in result.content[0].text
    assert vault.notes[RAG] == before


def test_a_new_node_may_link_to_itself_and_to_existing_nodes(vault):
    result = call(
        "create_node",
        {
            "title": "Vector Database",
            "type": "concept",
            "synthesis": "A [[vector databases|vector DB]] serves [[RAG]] retrieval.",
        },
    )
    assert not result.is_error
    assert (
        "A [[Vector Database|vector DB]] serves [[Retrieval-Augmented Generation|RAG]] retrieval."
        in vault.notes["Concepts/Vector Database.md"]
    )


# ---------------------------------------------------------------- 10.4 what a Node says, and safe rewrites
def test_find_node_shows_the_current_synthesis_and_recent_evidence(vault):
    for n in range(12):
        call(
            "append_evidence",
            {"path": RAG, "source": f"Post {n}", "text": f"Point {n}."},
        )
    found = call("find_node", {"name": "RAG"}).structured_content
    assert found["synthesis"] == "Old synthesis."
    assert len(found["evidence"]) == 10  # only the newest 10 are shown
    assert (
        "[[Post 11]]" in found["evidence"][-1] and "[[Post 2]]" in found["evidence"][0]
    )


def test_a_rewrite_that_drops_most_of_the_synthesis_is_refused(vault):
    long = "Retrieval first, then generation. " * 10
    assert not call("update_synthesis", {"path": RAG, "synthesis": long}).is_error
    before = vault.notes[RAG]
    result = call("update_synthesis", {"path": RAG, "synthesis": "Short."})
    assert result.is_error and "much shorter" in result.content[0].text
    assert vault.notes[RAG] == before


def test_a_rewrite_may_grow_or_shrink_a_little(vault):
    long = "Retrieval first, then generation. " * 10
    call("update_synthesis", {"path": RAG, "synthesis": long})
    tighter = long[: int(len(long) * 0.7)]
    assert not call("update_synthesis", {"path": RAG, "synthesis": tighter}).is_error


def test_a_contradiction_is_flagged_in_the_evidence(vault):
    call(
        "append_evidence",
        {
            "path": RAG,
            "source": "Skeptic",
            "text": "RAG rarely helps.",
            "contradicts": True,
        },
    )
    assert "⚠️ **Contradicts earlier evidence.** RAG rarely helps." in section(
        vault.notes[RAG], "## Evidence"
    )
