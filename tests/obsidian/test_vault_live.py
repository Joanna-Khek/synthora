"""Integration tests: need Obsidian running and the synthora-test vault registered.

Run with: uv run pytest -m obsidian
"""

import time
import uuid
from pathlib import Path

import pytest
from datetime import date

from synthora.obsidian.notes import EvidenceEntry, NodeNote
from synthora.obsidian.sections import MyNotesViolation, append_to_section

from synthora.obsidian.vault import Vault

pytestmark = pytest.mark.obsidian
TEST_VAULT = Path.home() / "Vaults" / "synthora-test"


def eventually(check, timeout: float = 3.0):
    """Obsidian indexes in the background: retry a check until it passes or times out."""
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(0.1)


@pytest.fixture
def vault():
    v = Vault("synthora-test", TEST_VAULT)
    folder = f"Tests/{uuid.uuid4().hex[:8]}"
    yield v, folder
    for path in v._run("files", folder=folder).splitlines():
        v._run(
            "delete", path=path, permanent=True
        )  # via the CLI, so the index stays in sync


def test_create_then_read(vault):
    v, folder = vault
    v.create(f"{folder}/a.md", "hello\nworld")
    assert "hello\nworld" in v.read(f"{folder}/a.md")


def test_backslashes_survive_a_direct_write(vault):
    v, folder = vault
    v.create(f"{folder}/win.md", r"path: C:\Users\joann")
    assert r"C:\Users\joann" in v.read(f"{folder}/win.md")


def test_large_body_is_indexed_after_a_direct_write(vault):
    v, folder = vault
    v.create(f"{folder}/big.md", "x" * 5000)
    assert v.exists(f"{folder}/big.md")


def test_creating_twice_is_refused(vault):
    v, folder = vault
    v.create(f"{folder}/a.md", "first")
    with pytest.raises(FileExistsError):
        v.create(f"{folder}/a.md", "second")


def test_backlinks(vault):
    v, folder = vault
    name = f"Concept-{folder[-8:]}"  # unique in the whole vault, so [[name]] can't resolve elsewhere
    v.create(f"{folder}/{name}.md", "A concept.")
    v.create(f"{folder}/source.md", f"Uses [[{name}]].")
    assert eventually(lambda: v.backlinks(f"{folder}/{name}.md")) == [
        f"{folder}/source.md"
    ]


def test_list_property_is_a_yaml_list_of_quoted_links(vault):
    v, folder = vault
    v.create(f"{folder}/a.md", "body")
    v.set_property(f"{folder}/a.md", "uses", ["[[RAG]]", "[[vLLM]]"])
    assert '  - "[[RAG]]"\n  - "[[vLLM]]"' in v.read(f"{folder}/a.md")


def _node(v, folder) -> str:
    path = f"{folder}/node.md"
    note = NodeNote(
        title="Guard Test",
        type="concept",
        synthesis="Old synthesis.",
        evidence=[EvidenceEntry(day=date(2026, 11, 16), source="Saw", text="First.")],
    )
    v.create(
        path, note.render() + "my own thoughts\n"
    )  # as if you'd typed under My Notes
    return path


def test_evidence_append_is_seen_by_obsidian_and_keeps_my_notes(vault):
    v, folder = vault
    path = _node(v, folder)

    v.edit(
        path,
        lambda t: append_to_section(
            t, "## Evidence", "- 2026-11-20 · [[Post]]: Second."
        ),
    )

    # read back through the CLI: proves Obsidian itself sees the direct write
    assert eventually(lambda: "Second." in v.read(path))
    text = v.read(path)
    assert text.index("Second.") < text.index("## My Notes")
    assert text.rstrip().endswith("my own thoughts")


def test_refused_edit_leaves_the_note_unchanged_in_obsidian(vault):
    v, folder = vault
    path = _node(v, folder)
    before = v.read(path)

    with pytest.raises(MyNotesViolation):
        v.edit(path, lambda t: t + "agent scribble\n")

    assert v.read(path) == before
