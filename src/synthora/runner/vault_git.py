"""FR-20: commit the Vault after each Capture, so `git revert` undoes exactly one."""

import subprocess
from pathlib import Path


class VaultGitError(Exception):
    """The Vault isn't a git repository, or git refused to commit."""


class VaultGit:
    def __init__(self, path: Path) -> None:
        self.path = path
        if not (path / ".git").is_dir():
            raise VaultGitError(
                f"{path} is not a git repository: run `git init` there first (FR-20)"
            )

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise VaultGitError(
                f"git {args[0]}: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    def commit(self, message: str) -> bool:
        """Commit every change in the Vault. Returns False if there was nothing to commit."""
        if not self._git("status", "--porcelain").strip():
            return False  # e.g. a re-run that found nothing new
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", message)
        return True
