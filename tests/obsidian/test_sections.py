from datetime import date

import pytest

from synthora.obsidian.notes import MY_NOTES, EvidenceEntry, NodeNote
from synthora.obsidian.sections import (
    MyNotesViolation,
    append_to_section,
    check_my_notes_untouched,
    replace_section,
    section,
)

USER_TEXT = "my own thoughts\n\n## A heading I added\nmore of mine\n"


def note() -> str:
    rendered = NodeNote(
        title="Retrieval-Augmented Generation",
        type="concept",
        synthesis="Old synthesis.",
        evidence=[EvidenceEntry(day=date(2026, 11, 16), source="Saw", text="First.")],
    ).render()
    return rendered + USER_TEXT  # as if you had written under My Notes


def test_replacing_synthesis_leaves_my_notes_identical():
    result = replace_section(note(), "## Synthesis", "New synthesis.")
    assert section(result, "## Synthesis") == "New synthesis."
    assert result.endswith(f"{MY_NOTES}\n{USER_TEXT}")


def test_evidence_is_appended_inside_its_own_section():
    result = append_to_section(
        note(), "## Evidence", "- 2026-11-20 · [[Post]]: Second."
    )
    assert section(result, "## Evidence").splitlines() == [
        "- 2026-11-16 · [[Saw]]: First.",
        "- 2026-11-20 · [[Post]]: Second.",
    ]
    assert result.endswith(f"{MY_NOTES}\n{USER_TEXT}")


def test_my_notes_cannot_be_targeted():
    with pytest.raises(MyNotesViolation):
        replace_section(note(), MY_NOTES, "agent text")


def test_smuggled_headings_are_refused():
    with pytest.raises(ValueError):
        replace_section(note(), "## Synthesis", "fine\n## My Notes\nagent text")


def test_your_own_headings_below_my_notes_are_protected():
    with pytest.raises(MyNotesViolation):
        replace_section(note(), "## A heading I added", "agent text")


def test_any_change_inside_my_notes_is_caught():
    with pytest.raises(MyNotesViolation):
        check_my_notes_untouched(note(), note().replace("my own", "the agent's"))


def test_a_note_without_my_notes_is_not_edited():
    with pytest.raises(MyNotesViolation):
        check_my_notes_untouched("## Synthesis\nx\n", "## Synthesis\ny\n")
