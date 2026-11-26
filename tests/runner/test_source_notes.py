from datetime import date

from synthora.queue import QueuedSource
from synthora.runner.source_notes import write_source_note
from synthora.domain import Distillation, SourceType
from synthora.obsidian.notes import split_frontmatter
from synthora.obsidian.sections import section


class FakeVault:
    def __init__(self) -> None:
        self.notes: dict[str, str] = {}

    def create(self, path: str, body: str) -> None:
        if path in self.notes:
            raise FileExistsError(path)
        self.notes[path] = body

    def read(self, path: str) -> str:
        return self.notes[path]


def item(**overrides) -> QueuedSource:
    fields = dict(
        id=1,
        url="https://example.com/post",
        source_type=SourceType.WEB_ARTICLE,
        title="Agents: a primer",
        captured=date(2026, 11, 25),
        comment="for the RAG talk",
        distillation=Distillation(
            tldr="Agents loop over tools.",
            key_ideas=["Loops.", "Tools."],
            notable_quotes=["Keep it simple."],
        ),
        full_text="...",
        parent_title=None,
    )
    return QueuedSource(**(fields | overrides))


def test_a_source_note_has_everything_the_queue_holds():
    vault = FakeVault()
    path = write_source_note(vault, item())
    assert path == "Sources/Agents a primer.md"  # ':' isn't allowed in a note name
    text = vault.notes[path]
    props, _ = split_frontmatter(text)
    assert props["type"] == "source" and props["source_type"] == "web_article"
    assert (
        props["url"] == "https://example.com/post"
        and str(props["captured"]) == "2026-11-25"
    )
    assert section(text, "## TL;DR") == "Agents loop over tools."
    assert section(text, "## Why I saved this") == "for the RAG talk"
    assert section(text, "## Key ideas") == "- Loops.\n- Tools."
    assert "Keep it simple." in section(text, "## Notable quotes")
    assert "## My Notes" in text


def test_a_linked_source_says_which_post_it_came_from():
    path = write_source_note(
        vault := FakeVault(), item(parent_title="A post: with links")
    )
    props, _ = split_frontmatter(vault.notes[path])
    assert props["saved_via"] == "[[A post with links]]"  # the post's note name


def test_running_again_reuses_the_note_for_the_same_url():
    vault = FakeVault()
    first = write_source_note(vault, item())
    again = write_source_note(vault, item())  # the run crashed before the ack
    assert again == first and len(vault.notes) == 1


def test_a_different_source_with_the_same_title_gets_a_number():
    vault = FakeVault()
    write_source_note(vault, item())
    other = write_source_note(vault, item(id=2, url="https://other.example/post"))
    assert other == "Sources/Agents a primer (2).md"
    assert len(vault.notes) == 2


def test_a_capture_without_title_or_type_still_gets_a_note():
    old = item(
        title=None,
        source_type=None,
        url="https://github.com/org/repo",
        distillation=Distillation(tldr="old gist", key_ideas=[], notable_quotes=[]),
    )
    path = write_source_note(vault := FakeVault(), old)
    props, _ = split_frontmatter(vault.notes[path])
    assert props["source_type"] == "github_repo"  # guessed from the URL
    assert path.startswith("Sources/")
