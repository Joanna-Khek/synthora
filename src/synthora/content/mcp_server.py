import re
from datetime import date
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated, Literal, cast, get_args
from pathlib import PurePosixPath

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from synthora.config import Settings
from synthora.obsidian.vault import ObsidianError, Vault, NoteBusy
from synthora.obsidian.notes import (
    EvidenceEntry,
    NodeNote,
    Relation,
    link,
    name_key,
    split_frontmatter,
    join_frontmatter,
)
from synthora.obsidian.sections import (
    MyNotesViolation,
    append_to_section,
    replace_section,
    section,
)
from synthora.obsidian import file_vault

NOTE_FOLDERS = (
    "Sources",
    "Concepts",
    "Entities",
)  # PRD §5.1: every note the tools may read
NODE_FOLDERS = ("Concepts", "Entities")  # the notes the tools may edit
SYNTHESIS = "## Synthesis"
EVIDENCE = "## Evidence"
MAX_EVIDENCE_SHOWN = 10  # find_node shows the newest entries; the note keeps them all
MIN_REWRITE_RATIO = 0.5  # a new Synthesis may not be shorter than half the old one


class NodeMatch(BaseModel):
    path: str
    matched_by: Literal["title", "alias"]


class NodeLookup(BaseModel):
    match: NodeMatch | None
    similar: list[str] = []  # Nodes sharing a word with the name: maybe the same thing
    # What the matched Node already says (FR-17): read before you update it
    synthesis: str | None = None
    evidence: list[str] = []  # the most recent entries, oldest first


def open_vault() -> Vault:
    """Separate function so tests can swap in a fake Vault."""
    config = Settings().vault
    if config is None:
        raise RuntimeError(
            "no `vault:` in config.yaml; the Content Server runs on the laptop only"
        )
    return file_vault.open_vault(config)


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncGenerator[Vault]:
    yield open_vault()


mcp = MCPServer(
    name="synthora-content",
    instructions=(
        "Tools for reading the Synthora Vault (an Obsidian vault of Sources, Concepts "
        "and Entities). No tool fetches URLs or changes a '## My Notes' section."
    ),
    lifespan=lifespan,
)


def _vault(ctx: Context) -> Vault:
    return cast(Vault, ctx.request_context.lifespan_context)


@contextmanager
def _tool_errors() -> Generator[None]:
    """Expected failures become ToolErrors, so the agent sees the reason and can adapt."""
    try:
        yield
    except NoteBusy as e:  # FR-25: not a failure; the Capture is retried later
        raise ToolError(
            f"{e}. Leave this note alone for now; don't retry or work around it."
        ) from e
    except MyNotesViolation as e:
        raise ToolError(f"refused: {e}. ## My Notes belongs to the user.") from e
    except FileExistsError as e:
        raise ToolError(f"{e} already exists; link to it or edit it instead") from e
    except (ObsidianError, ValueError) as e:
        raise ToolError(str(e)) from e


def _note_path(path: str) -> str:
    """Accept only 'Folder/Name.md' directly inside one of the note folders."""
    folder, _, filename = path.partition("/")
    if (
        folder not in NOTE_FOLDERS
        or not filename.endswith(".md")
        or "/" in filename
        or "\\" in filename
        or filename.startswith(".")
    ):
        raise ToolError(
            f"{path!r} is not a note path like 'Concepts/Name.md' "
            f"(folders: {', '.join(NOTE_FOLDERS)})"
        )
    return path


NODE_PREFIXES = tuple(f"{f}/" for f in NODE_FOLDERS)
_STOPWORDS = {"a", "an", "the", "of", "and", "or", "for", "in", "on", "to", "with"}


def _words(name: str) -> set[str]:
    return {w for w in name_key(name).split() if w not in _STOPWORDS}


def _lookup(vault: Vault, name: str, max_similar: int = 5) -> NodeLookup:
    """Same key → a match. Otherwise, Nodes sharing a word → candidates for the agent to judge."""
    names: list[tuple[str, str, Literal["title", "alias"]]] = [
        (PurePosixPath(path).stem, path, "title")
        for folder in NODE_FOLDERS
        for path in vault.list_notes(folder)
    ]
    names.extend(
        (a, p, "alias")
        for a, p in vault.aliases().items()
        if p.startswith(NODE_PREFIXES)
    )
    key = name_key(name)
    for other, path, kind in names:  # titles come first, so a title beats an alias
        if name_key(other) == key:
            return NodeLookup(match=NodeMatch(path=path, matched_by=kind))

    wanted = _words(name)
    shared: dict[str, int] = {}
    for other, path, _ in names:
        if n := len(wanted & _words(other)):
            shared[path] = max(shared.get(path, 0), n)
    similar = sorted(shared, key=lambda p: (-shared[p], p))[:max_similar]
    return NodeLookup(match=None, similar=similar)


def _canonical(vault: Vault, name: str) -> str:
    """The canonical title of an existing Node (FR-14: links target titles, never aliases)."""
    match = _lookup(vault, name).match
    if match is None:
        raise ToolError(
            f"relation target {name!r} isn't a Node yet: create it first, or leave the relation out"
        )
    return PurePosixPath(match.path).stem


def _require_node(vault: Vault, path: str) -> None:
    if path not in vault.list_notes(path.split("/")[0]):
        raise ToolError(f"{path} doesn't exist")


# [[target#heading|display]] and ![[embed]]: group 1 is "!" for an embed
_WIKILINK = re.compile(r"(!?)\[\[([^\[\]|#]*)(#[^\[\]|]*)?(?:\|([^\[\]]*))?\]\]")


def _canonical_links(vault: Vault, text: str, *, own_title: str | None = None) -> str:
    """Make every [[link]] in agent-written text point at a Node's canonical title (FR-14).

    [[RAG]] becomes [[Retrieval-Augmented Generation|RAG]]: the link resolves, and the
    reader still sees the name the agent wrote. Embeds, same-note links and links to
    notes that don't exist are refused, with a reason the agent can act on.
    """

    def fix(m: re.Match) -> str:
        embed, target, heading, display = m.groups()
        target = target.strip()
        if embed:
            raise ToolError(
                f"embeds like {m.group(0)!r} aren't allowed; write a [[link]] instead"
            )
        if not target:
            raise ToolError(f"same-note links like {m.group(0)!r} aren't allowed")
        if own_title is not None and name_key(target) == name_key(own_title):
            title = own_title  # the Node being created links to itself
        else:
            match = _lookup(vault, target).match
            if match is None:
                raise ToolError(
                    f"{m.group(0)} isn't a Node: find or create it first, or write it as plain text"
                )
            title = PurePosixPath(match.path).stem
        shown = (
            display if display is not None else (target if target != title else None)
        )
        return f"[[{title}{heading or ''}{'|' + shown if shown else ''}]]"

    return _WIKILINK.sub(fix, text)


def _section_or_none(text: str, heading: str) -> str | None:
    """A section's text, or None for a note without that section (e.g. one written by hand)."""
    try:
        return section(text, heading)
    except ValueError:
        return None


@mcp.tool()
def find_node(name: str, ctx: Context) -> NodeLookup:
    """Look up a Concept or Entity by its title or one of its aliases.

    Matching ignores case, hyphens, plurals and British/US spelling.
    - match: the existing Node. Link to its title instead of creating a new one.
    - similar: when there is no match, existing Nodes that share a word with the
      name. If one of them is the same thing under another name ("Vector DB" and
      "Vector Database"), use it and call add_alias; otherwise create the Node.
    - synthesis / evidence: what the matched Node already says. Read them before
      updating it: append_evidence always, update_synthesis if this Source adds something.
    Call this before creating any Node.
    """
    vault = _vault(ctx)
    with _tool_errors():
        found = _lookup(vault, name)
        if found.match is None:
            return found
        text = vault.read(found.match.path)
        evidence = _section_or_none(text, EVIDENCE) or ""
        entries = [line for line in evidence.splitlines() if line.startswith("- ")]
        return found.model_copy(
            update={
                "synthesis": _section_or_none(text, SYNTHESIS),
                "evidence": entries[-MAX_EVIDENCE_SHOWN:],
            }
        )


@mcp.tool()
def search_vault(
    query: str, ctx: Context, limit: Annotated[int, Field(ge=1, le=50)] = 10
) -> list[str]:
    """Full-text search over note bodies, titles and properties. Returns note paths.

    Use it to discover related notes. To check whether a Node exists, use find_node.
    """
    if not query.strip():
        raise ToolError("query is empty")
    with _tool_errors():
        return _vault(ctx).search(query, limit=limit)


@mcp.tool()
def read_note(path: str, ctx: Context) -> str:
    """Read a note's full Markdown (frontmatter included), e.g. path='Concepts/Retrieval-Augmented Generation.md'."""
    with _tool_errors():
        return _vault(ctx).read(_note_path(path))


@mcp.tool()
def list_backlinks(path: str, ctx: Context) -> list[str]:
    """Paths of the notes that link to this note."""
    with _tool_errors():
        return _vault(ctx).backlinks(_note_path(path))


@mcp.tool()
def relation_vocabulary() -> list[str]:
    """The only Relation names a note may use (FR-16), e.g. 'uses' or 'is_a'."""
    return list(get_args(Relation))


# Writing tools
def _node_path(path: str) -> str:
    path = _note_path(path)
    if not path.startswith(tuple(f"{f}/" for f in NODE_FOLDERS)):
        raise ToolError(
            f"{path!r} is not a Concept or Entity; only Nodes can be edited"
        )
    return path


@mcp.tool()
def create_node(
    title: str,
    type: Literal["concept", "entity"],
    synthesis: str,
    ctx: Context,
    aliases: list[str] = [],
    relations: dict[Relation, list[str]] = {},
) -> str:
    """Create a new Concept or Entity note and return its path.

    title is the canonical full name ("Retrieval-Augmented Generation"); put
    abbreviations and other names in aliases ("RAG"). Refused if the title or any
    alias already belongs to a Node: link to that Node instead. Add evidence
    afterwards with append_evidence.
    """
    vault = _vault(ctx)
    for name in [title, *aliases]:
        with _tool_errors():
            match = _lookup(vault, name).match
        if match:
            raise ToolError(
                f"{name!r} already exists as {match.path} (matched by {match.matched_by}); "
                "link to it instead of creating a new Node"
            )
    folder = "Concepts" if type == "concept" else "Entities"
    with _tool_errors():  # NodeNote's validation errors (bad title, ...) are ValueErrors too
        targets = {
            relation: list(dict.fromkeys(_canonical(vault, name) for name in names))
            for relation, names in relations.items()
        }
        synthesis = _canonical_links(vault, synthesis, own_title=title)
        note = NodeNote(
            title=title,
            type=type,
            synthesis=synthesis,
            aliases=aliases,
            relations=targets,
        )
        path = f"{folder}/{note.title}.md"
        vault.create(path, note.render())
    return path


@mcp.tool()
def update_synthesis(path: str, synthesis: str, ctx: Context) -> str:
    """Rewrite a Node's Synthesis to include what a new Source adds (FR-17).

    Send the WHOLE new Synthesis: everything the earlier Evidence supports, plus the
    new point. A rewrite much shorter than the current one is refused, so knowledge
    can't be lost by accident. No ## headings.
    """
    vault = _vault(ctx)
    with _tool_errors():
        synthesis = _canonical_links(vault, synthesis)

        def rewrite(text: str) -> str:
            old = section(text, SYNTHESIS)
            if len(synthesis.strip()) < MIN_REWRITE_RATIO * len(old):
                raise ToolError(
                    f"the new Synthesis ({len(synthesis.strip())} chars) is much shorter than "
                    f"the current one ({len(old)} chars): keep what the earlier Evidence "
                    "supports and add to it"
                )
            return replace_section(text, SYNTHESIS, synthesis)

        vault.edit(_node_path(path), rewrite)
    return f"updated the Synthesis of {path}"


@mcp.tool()
def append_evidence(
    path: str, source: str, text: str, ctx: Context, contradicts: bool = False
) -> str:
    """Add one dated Evidence line to a Node (FR-17). Evidence is append-only.

    source is the Source Note's title the evidence comes from. Set contradicts=true
    when it disagrees with earlier evidence on this Node. A Node holds at most one
    entry per Source; adding a second one is refused.
    """
    vault = _vault(ctx)
    with _tool_errors():
        text = _canonical_links(vault, text)
        entry = EvidenceEntry(
            day=date.today(), source=source, text=text, contradicts=contradicts
        )

        def add(body: str) -> str:
            # One entry per Source per Node: re-running a Source (a retry) changes nothing.
            if link(entry.source) in section(body, EVIDENCE):
                raise ToolError(
                    f"{path} already has evidence from {link(entry.source)}; nothing to add"
                )
            return append_to_section(body, EVIDENCE, entry.render())

        vault.edit(_node_path(path), add)
    return f"added evidence to {path}"


@mcp.tool()
def add_alias(path: str, alias: str, ctx: Context) -> str:
    """Record another name for an existing Node, so find_node matches it next time.

    Use it when find_node found no match, but a similar Node is the same thing,
    e.g. path='Concepts/Vector Database.md', alias='Vector DB'.
    """
    path = _node_path(path)
    alias = " ".join(alias.split())
    if not alias or len(alias) > 100 or "[" in alias:
        raise ToolError("an alias is a short plain name, without [[links]]")
    vault = _vault(ctx)
    with _tool_errors():
        if path not in vault.list_notes(path.split("/")[0]):
            raise ToolError(f"{path} doesn't exist")
        if existing := _lookup(vault, alias).match:
            if existing.path == path:
                return f"{alias!r} already finds {path}; nothing to add"
            raise ToolError(f"{alias!r} already belongs to {existing.path}")
        current = [a for a, p in vault.aliases().items() if p == path]
        vault.set_property(path, "aliases", [*current, alias])
    return f"added alias {alias!r} to {path}"


@mcp.tool()
def add_relation(path: str, relation: Relation, target: str, ctx: Context) -> str:
    """Relate an existing Node to another existing Node (FR-16).

    e.g. path='Concepts/Hierarchical Navigable Small World.md', relation='is_a',
    target='Approximate Nearest Neighbour Search'. The target may be given by an
    alias; it is stored under its canonical title.
    """
    path = _node_path(path)
    vault = _vault(ctx)
    with _tool_errors():
        _require_node(vault, path)
        target_link = link(_canonical(vault, target))
        if target_link == link(PurePosixPath(path).stem):
            raise ToolError("a Node can't be related to itself")
        already = False

        def add(text: str) -> str:
            nonlocal already
            props, body = split_frontmatter(text)
            current = props.get(relation) or []
            if target_link in current:
                already = True
                return text
            props[relation] = [*current, target_link]
            return join_frontmatter(props, body)

        vault.edit(path, add)
    if already:
        return f"{path} already has {relation} {target_link}; nothing to add"
    return f"added {relation} {target_link} to {path}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
