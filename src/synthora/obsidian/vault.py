"""Typed wrapper around the official Obsidian CLI (FR-19).

The CLI exits 0 even when it fails, so every call's output is checked for errors.
"""

import shutil
import subprocess
from pathlib import Path
import json
import time
from collections.abc import Callable

from synthora.obsidian.sections import check_my_notes_untouched

MAX_CLI_CONTENT_BYTES = 4000  # S2: longer content= values get mangled


class ObsidianError(Exception):
    """The CLI reported a failure, or a call was refused before reaching it."""


def _find_cli() -> str:
    # shutil.which finds obsidian.com (the CLI) before obsidian.exe (the app window).
    cli = shutil.which("obsidian")
    if cli is None:
        raise ObsidianError(
            "Obsidian CLI not on PATH: enable it in Settings -> General -> Advanced"
        )
    return cli


def _check(output: str) -> str:
    """Raise if the output is one of the CLI's failure messages."""
    text = output.strip()
    if text.startswith("Error:") or text == "Vault not found.":
        raise ObsidianError(text)
    return output


def _is_empty(output: str) -> bool:
    """'No matches found.', 'No backlinks found.', 'No links found.'"""
    text = output.strip()
    return text.startswith("No ") and text.endswith(" found.")


def _fits_cli(text: str) -> bool:
    """Can this text go through content= unharmed?"""
    return "\\" not in text and len(text.encode("utf-8")) <= MAX_CLI_CONTENT_BYTES


def _escape(text: str) -> str:
    """Real newlines/tabs → the \\n / \\t escapes the CLI expects."""
    return text.replace("\r\n", "\n").replace("\n", "\\n").replace("\t", "\\t")


class Vault:
    def __init__(self, name: str, path: Path, *, timeout: float = 30) -> None:
        self.name = name
        self.path = path.resolve()
        self.timeout = timeout
        self._cli = _find_cli()
        self._check_identity()

    def _run(self, command: str, **params: str | int | bool | None) -> str:
        """Run one CLI command; return stdout, or raise ObsidianError."""
        args = [self._cli, f"vault={self.name}", command]
        for key, value in params.items():
            if value is True:
                args.append(key)  # a flag, e.g. overwrite
            elif value not in (False, None):
                args.append(f"{key}={value}")
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout
        )
        if result.returncode != 0:
            raise ObsidianError(
                f"{command}: exit {result.returncode}: {result.stderr.strip()}"
            )
        return _check(result.stdout)

    def _check_identity(self) -> None:
        """Make sure vault=<name> really is the folder we expect."""
        lines = self._run("vault").splitlines()
        info = dict(line.split("\t", 1) for line in lines if "\t" in line)
        actual = info.get("path", "")
        if Path(actual).resolve() != self.path:
            raise ObsidianError(
                f"vault={self.name!r} is {actual!r}, not {str(self.path)!r}. "
                "Do two vaults share this name?"
            )

    def _abs(self, path: str) -> Path:
        """Vault-relative path → absolute path, refusing anything outside the vault."""
        target = (self.path / path).resolve()
        if not target.is_relative_to(self.path):
            raise ObsidianError(f"{path!r} is outside the vault")
        return target

    def read(self, path: str) -> str:
        self._abs(path)  # guard only
        return self._run("read", path=path)

    def create(self, path: str, body: str) -> None:
        """Create a new note. Never overwrites, and never lets the CLI rename it."""
        target = self._abs(path)
        if target.exists() or self.exists(path):  # disk OR index: they can disagree
            raise FileExistsError(path)
        if _fits_cli(body):
            out = self._run("create", path=path, content=_escape(body)).strip()
            if out != f"Created: {path}":
                raise ObsidianError(
                    f"asked to create {path!r}, but the CLI said {out!r}"
                )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as f:
                f.write(body)
            self._wait_until_indexed(path)

    def edit(self, path: str, change: Callable[[str], str]) -> None:
        """Change an existing note's text. The ONLY way to modify a note's body.

        `change` gets the current text and returns the new text. The write is refused
        if it would alter ## My Notes (FR-18), or if the file changed while we worked.
        """
        target = self._abs(path)
        before = target.read_text(encoding="utf-8")
        after = change(before)
        check_my_notes_untouched(before, after)
        if target.read_text(encoding="utf-8") != before:
            raise ObsidianError(
                f"{path!r} changed while it was being edited; try again later"
            )
        target.write_text(after, encoding="utf-8", newline="\n")

    def set_property(self, path: str, name: str, value: str | list[str]) -> None:
        """Set one frontmatter property. A list becomes a YAML list of quoted items (FR-19b)."""
        self._abs(path)
        if isinstance(value, list):
            raw, kind = (
                json.dumps(value, ensure_ascii=False),
                "list",
            )  # the CLI needs a JSON array
        else:
            raw, kind = value, "text"
        if "\\" in raw:
            raise ObsidianError(
                f"backslashes in property values aren't supported: {raw!r}"
            )
        out = self._run(
            "property:set", path=path, name=name, value=raw, type=kind
        ).strip()
        if not out.startswith(f"Set {name}:"):
            raise ObsidianError(f"property:set on {path!r}: the CLI said {out!r}")

    def exists(self, path: str) -> bool:
        """Does Obsidian's index know this note? It can lag behind the disk."""
        self._abs(path)
        try:
            self._run("file", path=path)
        except ObsidianError as e:
            if "not found" in str(e):
                return False
            raise
        return True

    def _wait_until_indexed(self, path: str, timeout: float = 5.0) -> None:
        """After a direct write, wait until the CLI can see the note."""
        deadline = time.monotonic() + timeout
        while not self.exists(path):
            if time.monotonic() > deadline:
                raise ObsidianError(f"Obsidian didn't index {path!r} within {timeout}s")
            time.sleep(0.1)

    def search(
        self, query: str, *, folder: str | None = None, limit: int | None = None
    ) -> list[str]:
        """Paths of notes matching the query (body, filename or properties)."""
        out = self._run(
            "search", query=query, path=folder, limit=limit, format="json"
        ).strip()
        if out == "No matches found.":
            return []
        return json.loads(out)

    def aliases(self) -> dict[str, str]:
        """Every alias in the vault → the note that declares it (FR-13)."""
        out = self._run("aliases", verbose=True)
        return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)

    def backlinks(self, path: str) -> list[str]:
        """Paths of notes that link to this one."""
        self._abs(path)
        out = self._run("backlinks", path=path, format="json")
        if _is_empty(out):
            return []
        return [item["file"] for item in json.loads(out)]

    def links(self, path: str) -> list[str]:
        """Paths of notes this one links to."""
        self._abs(path)
        out = self._run("links", path=path)
        return [] if _is_empty(out) else out.strip().splitlines()
