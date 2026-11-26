import os
import time

import pytest

from synthora.config import VaultConfig
from synthora.obsidian.file_vault import FileVault, open_vault
from synthora.obsidian.sections import MyNotesViolation
from synthora.obsidian.vault import NoteBusy, ObsidianError


def _write(root, path, text, age=3600):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    old = time.time() - age  # old by default, so FR-25 doesn't think you're typing
    os.utime(p, (old, old))


@pytest.fixture
def vault(tmp_path):
    _write(
        tmp_path,
        "Concepts/RAG.md",
        "---\naliases:\n- Retrieval-Augmented Generation\n---\n"
        "## Synthesis\nretrieval and generation\n\n## My Notes\nmine\n",
    )
    _write(
        tmp_path,
        "Sources/Paper.md",
        "---\nuses:\n- '[[RAG]]'\n---\nA paper about [[RAG|retrieval]].\n",
    )
    _write(
        tmp_path,
        "Sources/Other.md",
        "Links to [[Concepts/RAG#Synthesis]] and generation, generation, generation.\n",
    )
    _write(tmp_path, "Sources/Unrelated.md", "Nothing here.\n")
    _write(tmp_path, ".obsidian/hidden.md", "[[RAG]] generation\n")
    return FileVault("synthora", tmp_path, busy_seconds=120)


def test_needs_an_existing_folder(tmp_path):
    with pytest.raises(ObsidianError, match="doesn't exist"):
        FileVault("x", tmp_path / "missing")


def test_read_and_missing(vault):
    assert "retrieval" in vault.read("Concepts/RAG.md")
    with pytest.raises(ObsidianError, match="not found"):
        vault.read("Concepts/Nope.md")


def test_refuses_paths_outside_the_vault(vault):
    with pytest.raises(ObsidianError, match="outside"):
        vault.read("../secret.md")


def test_create_never_overwrites(vault):
    vault.create("Concepts/New.md", "hello\n")
    assert vault.read("Concepts/New.md") == "hello\n"
    with pytest.raises(FileExistsError):
        vault.create("Concepts/New.md", "again\n")


def test_aliases(vault):
    assert vault.aliases() == {"Retrieval-Augmented Generation": "Concepts/RAG.md"}


def test_backlinks_by_name_alias_heading_folder_and_property(vault):
    # .obsidian/ is skipped, and Unrelated has no link
    assert vault.backlinks("Concepts/RAG.md") == [
        "Sources/Other.md",
        "Sources/Paper.md",
    ]


def test_search_all_terms_most_matches_first(vault):
    assert vault.search("generation") == ["Sources/Other.md", "Concepts/RAG.md"]
    assert vault.search("retrieval generation") == ["Concepts/RAG.md"]
    assert vault.search('"nothing here"') == ["Sources/Unrelated.md"]
    assert vault.search("generation", limit=1) == ["Sources/Other.md"]
    assert vault.search("generation", folder="Concepts") == ["Concepts/RAG.md"]
    assert "Concepts/RAG.md" in vault.search("rag")  # the file name counts too


def test_set_property_keeps_body(vault):
    vault.set_property(
        "Concepts/RAG.md", "aliases", ["Retrieval-Augmented Generation", "RAG system"]
    )
    assert vault.aliases()["RAG system"] == "Concepts/RAG.md"
    assert vault.read("Concepts/RAG.md").endswith("## My Notes\nmine\n")
    vault.set_property("Sources/Unrelated.md", "status", "read")  # no frontmatter yet
    assert (
        vault.read("Sources/Unrelated.md") == "---\nstatus: read\n---\nNothing here.\n"
    )


def test_edit_still_guards_my_notes_and_busy_notes(vault, tmp_path):
    with pytest.raises(MyNotesViolation):
        vault.edit("Concepts/RAG.md", lambda t: t.replace("mine", "changed"))
    _write(tmp_path, "Concepts/Fresh.md", "## My Notes\n", age=5)  # you just edited it
    with pytest.raises(NoteBusy):
        vault.edit("Concepts/Fresh.md", lambda t: t + "more\n")


def test_our_own_create_is_not_busy(vault):
    vault.create("Concepts/Mine.md", "## Synthesis\na\n\n## My Notes\n")
    vault.edit("Concepts/Mine.md", lambda t: t.replace("a\n", "b\n", 1))
    assert "b\n" in vault.read("Concepts/Mine.md")


def test_open_vault_picks_the_backend(tmp_path):
    config = VaultConfig(
        name="synthora",
        path=tmp_path,
        busy_seconds=120,
        git_commits=False,
        backend="files",
    )
    assert type(open_vault(config)) is FileVault
    default = VaultConfig(name="s", path=tmp_path, busy_seconds=1, git_commits=False)
    assert default.backend == "cli"
