from synthora.obsidian.notes import MY_NOTES


class MyNotesViolation(Exception):
    """A change would have modified the user's My Notes section."""


def _heading_index(lines: list[str], heading: str) -> int | None:
    return next(
        (i for i, line in enumerate(lines) if line.rstrip("\r\n") == heading), None
    )


def _bounds(lines: list[str], heading: str) -> tuple[int, int]:
    """(heading line, first line after the section): the section ends at the next ## heading."""
    start = _heading_index(lines, heading)
    if start is None:
        raise ValueError(f"no {heading!r} section")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return start, end


def section(text: str, heading: str) -> str:
    """The text under a heading, without the heading or surrounding blank lines."""
    lines = text.splitlines(keepends=True)
    start, end = _bounds(lines, heading)
    return "".join(lines[start + 1 : end]).strip("\n")


def my_notes(text: str) -> str:
    """Everything from the ## My Notes heading to the END of the note.

    To the end, not to the next heading: anything below it, including
    your own ## headings, is yours.
    """
    lines = text.splitlines(keepends=True)
    start = _heading_index(lines, MY_NOTES)
    if start is None:
        raise MyNotesViolation("no '## My Notes' heading: refusing to edit this note")
    return "".join(lines[start:])


def check_my_notes_untouched(before: str, after: str) -> None:
    if my_notes(before) != my_notes(after):
        raise MyNotesViolation("this change would modify ## My Notes")


def replace_section(text: str, heading: str, new_body: str) -> str:
    """Replace the text under `heading`, e.g. a Node's Synthesis (FR-17)."""
    if heading == MY_NOTES:
        raise MyNotesViolation("agents never edit ## My Notes")
    if any(line.startswith("## ") for line in new_body.splitlines()):
        raise ValueError("section text can't contain ## headings")
    lines = text.splitlines(keepends=True)
    start, end = _bounds(lines, heading)
    result = (
        "".join(lines[: start + 1])
        + new_body.strip("\n")
        + "\n\n"
        + "".join(lines[end:])
    )
    check_my_notes_untouched(text, result)
    return result


def append_to_section(text: str, heading: str, addition: str) -> str:
    """Add lines at the end of a section, e.g. one Evidence entry (FR-17)."""
    current = section(text, heading)
    return replace_section(
        text, heading, f"{current}\n{addition}" if current else addition
    )
