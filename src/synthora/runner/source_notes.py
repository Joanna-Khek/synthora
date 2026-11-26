"""11.1: write a Source Note for one queued Source, safely on a re-run."""

from itertools import count

from synthora.domain import guess_source_type
from synthora.obsidian.notes import SourceNote, safe_note_name, split_frontmatter
from synthora.obsidian.vault import Vault
from synthora.queue import QueuedSource

SOURCES = "Sources"


def source_note(item: QueuedSource) -> SourceNote:
    """PRD §5.2, from what the queue holds."""
    d = item.distillation
    return SourceNote(
        title=safe_note_name(item.title or item.url),
        source_type=item.source_type or guess_source_type(item.url),
        url=item.url,
        captured=item.captured,
        tldr=d.tldr,
        key_ideas=d.key_ideas,
        notable_quotes=d.notable_quotes,
        comment=item.comment,
        saved_via=safe_note_name(item.parent_title) if item.parent_title else None,
    )


def _url_of(vault: Vault, path: str) -> str | None:
    props, _ = split_frontmatter(vault.read(path))
    return props.get("url")


def write_source_note(vault: Vault, item: QueuedSource) -> str:
    """Create the Source Note and return its path, e.g. 'Sources/Some post.md'.

    Safe to run again: if a note for this same URL exists (an earlier run wrote it,
    then crashed before the ack), that note is used. A different Source with the
    same title gets ' (2)', ' (3)', ... instead of overwriting it.
    """
    note = source_note(item)
    body = note.render()
    for n in count(1):
        name = note.title if n == 1 else f"{note.title} ({n})"
        path = f"{SOURCES}/{name}.md"
        try:
            vault.create(path, body)
            return path
        except FileExistsError:
            if _url_of(vault, path) == item.url:
                return path  # written by an earlier run that didn't finish
    raise AssertionError("unreachable")
