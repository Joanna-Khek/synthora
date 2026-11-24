import re
from datetime import date
from typing import Annotated, Literal

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict

from synthora.server.schemas import SourceType

Relation = Literal[
    "is_a", "part_of", "uses", "improves_on", "contrasts_with", "introduced_by"
]  # FR-16
MY_NOTES = "## My Notes"  # 9.5's write guard keys on this exact heading

# Illegal in Windows filenames, or breaks Obsidian link syntax
_FORBIDDEN = re.compile(r'[\\/:*?"<>|#^\[\]]')


def safe_note_name(title: str, max_len: int = 120) -> str:
    """Turn any title into a valid note name (and filename, minus .md)."""
    name = " ".join(_FORBIDDEN.sub(" ", title).split())[:max_len]
    return name.rstrip(". ") or "Untitled"  # Windows drops trailing dots/spaces


def _check_name(name: str) -> str:
    cleaned = safe_note_name(name)
    if name != cleaned:
        raise ValueError(
            f"not a valid note name: {name!r} (cleaned, it would be {cleaned!r})"
        )
    return name


NoteName = Annotated[str, AfterValidator(_check_name)]


def link(name: str) -> str:
    return f"[[{name}]]"


def _frontmatter(properties: dict) -> str:
    dumped = yaml.safe_dump(
        properties,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10**6,
    )  # width: never fold a long title onto two lines
    return f"---\n{dumped}---\n"


def _relation_properties(relations: dict[str, list[str]]) -> dict[str, list[str]]:
    return {rel: [link(n) for n in names] for rel, names in relations.items() if names}


def _blockquote(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.splitlines())


class SourceNote(BaseModel):
    """PRD §5.2: one per successful Capture."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: NoteName
    source_type: SourceType
    url: str
    captured: date
    tldr: str
    key_ideas: list[str]
    notable_quotes: list[str] = []
    comment: str | None = None  # the Capture Comment
    saved_via: NoteName | None = None  # only on a Linked Capture
    aliases: list[str] = []
    relations: dict[Relation, list[NoteName]] = {}

    def render(self) -> str:
        props: dict = {
            "type": "source",
            "source_type": self.source_type.value,
            "url": self.url,
            "captured": self.captured,
        }
        if self.saved_via:
            props["saved_via"] = link(self.saved_via)
        props["aliases"] = self.aliases
        props |= _relation_properties(self.relations)

        body = ["## TL;DR", self.tldr, ""]
        if self.comment:
            body += ["## Why I saved this", self.comment, ""]
        body += ["## Key ideas", *(f"- {idea}" for idea in self.key_ideas), ""]
        if self.notable_quotes:  # a blank line between quotes, or Markdown merges them
            body += [
                "## Notable quotes",
                "\n\n".join(map(_blockquote, self.notable_quotes)),
                "",
            ]
        related = list(
            dict.fromkeys(n for names in self.relations.values() for n in names)
        )
        if related:
            body += ["## Related", *(f"- {link(n)}" for n in related), ""]
        body += [MY_NOTES, ""]
        return _frontmatter(props) + "\n".join(body)


class EvidenceEntry(BaseModel):
    """One dated line in a Node's append-only Evidence section (FR-17)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date
    source: NoteName  # the Source Note it came from
    text: str
    contradicts: bool = False

    def render(self) -> str:
        flag = "⚠️ **Contradicts earlier evidence.** " if self.contradicts else ""
        text = " ".join(self.text.split())  # one line, so the list item can't break
        return f"- {self.day.isoformat()} · {link(self.source)}: {flag}{text}"


class NodeNote(BaseModel):
    """PRD §5.3: a Concept or an Entity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: NoteName
    type: Literal["concept", "entity"]
    synthesis: str
    aliases: list[str] = []
    relations: dict[Relation, list[NoteName]] = {}
    evidence: list[EvidenceEntry] = []

    def render(self) -> str:
        props = {"type": self.type, "aliases": self.aliases} | _relation_properties(
            self.relations
        )
        body = [
            "## Synthesis",
            self.synthesis,
            "",
            "## Evidence",
            *(e.render() for e in self.evidence),
            "",
            MY_NOTES,
            "",
        ]
        return _frontmatter(props) + "\n".join(body)
