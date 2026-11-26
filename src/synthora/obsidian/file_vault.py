"""A Vault on plain files: no Obsidian app needed, so it runs on the server.

It inherits the guards (FR-18 My Notes, FR-25 busy notes) and the plain-file
methods (edit, list_notes) from Vault, and replaces only what used the CLI.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import yaml

from synthora.config import VaultConfig
from synthora.obsidian.notes import join_frontmatter, split_frontmatter
from synthora.obsidian.vault import ObsidianError, Vault

# [[Target]], [[Target|shown]], [[Target#Heading]], [[Folder/Target]]: capture "Target..."
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
# "an exact phrase" or a single word
TERM = re.compile(r'"([^"]+)"|(\S+)')


class FileVault(Vault):
    def _connect(self) -> None:
        if not self.path.is_dir():
            raise ObsidianError(f"vault folder {str(self.path)!r} doesn't exist")

    # -------------------------------------------------------------- helpers
    def _notes(self) -> Iterator[Path]:
        """Every .md note, skipping hidden folders such as .obsidian and .trash."""
        for p in sorted(self.path.rglob("*.md")):
            if not any(part.startswith(".") for part in p.relative_to(self.path).parts):
                yield p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.path).as_posix()

    def _properties(self, p: Path) -> dict:
        try:
            return split_frontmatter(p.read_text(encoding="utf-8"))[0]
        except (ValueError, yaml.YAMLError):
            return {}  # no frontmatter, or broken YAML: no properties

    # -------------------------------------------------------------- reading
    def read(self, path: str) -> str:
        target = self._abs(path)
        if not target.is_file():
            raise ObsidianError(f'File "{path}" not found.')
        return target.read_text(encoding="utf-8")

    def exists(self, path: str) -> bool:
        return self._abs(path).is_file()

    def open_note_names(self) -> set[str]:
        return set()  # nobody has tabs open on a server

    def aliases(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for p in self._notes():
            value = self._properties(p).get("aliases") or []
            for alias in [value] if isinstance(value, str) else value:
                found[str(alias)] = self._rel(p)
        return found

    def backlinks(self, path: str) -> list[str]:
        target = self._abs(path)
        name = target.stem.casefold()
        result = []
        for p in self._notes():
            if p == target:
                continue
            links = WIKILINK.findall(p.read_text(encoding="utf-8"))
            # Obsidian links by name, so compare the last part without ".md"
            if any(
                link.strip().removesuffix(".md").rsplit("/", 1)[-1].casefold() == name
                for link in links
            ):
                result.append(self._rel(p))
        return result

    def search(
        self, query: str, *, folder: str | None = None, limit: int | None = None
    ) -> list[str]:
        """Notes containing every word (or "quoted phrase"), most matches first."""
        terms = [(a or b).casefold() for a, b in TERM.findall(query)]
        root = self._abs(folder) if folder else None
        scored = []
        for p in self._notes():
            if root and not p.is_relative_to(root):
                continue
            text = f"{self._rel(p)}\n{p.read_text(encoding='utf-8')}".casefold()
            if terms and all(t in text for t in terms):
                scored.append((-sum(text.count(t) for t in terms), self._rel(p)))
        return [path for _, path in sorted(scored)][:limit]

    # -------------------------------------------------------------- writing
    def create(self, path: str, body: str) -> None:
        target = self._abs(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open(
            "x", encoding="utf-8", newline="\n"
        ) as f:  # "x": never overwrite
            f.write(body)
        self._remember_write(target)

    def set_property(self, path: str, name: str, value: str | list[str]) -> None:
        text = self.read(path)
        try:
            props, body = split_frontmatter(text)
        except ValueError:
            props, body = {}, text  # no frontmatter yet: start one
        props[name] = value
        target = self._abs(path)
        target.write_text(join_frontmatter(props, body), encoding="utf-8", newline="\n")
        self._remember_write(target)


def open_vault(config: VaultConfig) -> Vault:
    """The Vault that config.yaml asks for: the Obsidian CLI, or plain files."""
    cls = FileVault if config.backend == "files" else Vault
    return cls(config.name, config.path, busy_seconds=config.busy_seconds)
