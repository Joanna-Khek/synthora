"""11.1: `uv run synthora-runner` writes everything that is queued into the Vault, once.

11.2 schedules it at login; for now, run it by hand.
"""

import os
import sys
from collections import Counter
from contextlib import ExitStack

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from synthora.cartographer.agent import (
    CartographerState,
    build_cartographer,
    system_prompt,
)
from synthora.config import Settings
from synthora.mcp_tools import load_tools
from synthora.obsidian.file_vault import open_vault
from synthora.runner.run import ItemResult, run_once
from synthora.runner.vault_git import VaultGit
from synthora.tracing import setup_tracing
from synthora.runner.cli import open_queue

CONTENT_SERVER = StdioServerParameters(
    command=sys.executable,
    args=["-m", "synthora.content.mcp_server"],
    env=dict(os.environ),
)


def _no_commit(message: str) -> None:
    """Git commits are switched off (vault.git_commits: false)."""


async def run() -> list[ItemResult]:
    settings = Settings()  # pyright: ignore[reportCallIssue]  (filled from .env and config.yaml)
    if settings.vault is None:
        sys.exit(
            "no `vault:` in config.yaml: the runner writes to the Vault on this laptop"
        )
    setup_tracing(settings.tracing)

    # FR-20 is opt-in: with git off, nothing is committed (and there's no per-Capture undo).
    commit = (
        VaultGit(settings.vault.path).commit
        if settings.vault.git_commits
        else _no_commit
    )
    vault = open_vault(settings.vault)
    model = ChatAnthropic(
        model=settings.llm.model,  # pyright: ignore[reportCallIssue]
        api_key=settings.llm_api_key.get_secret_value(),
        max_tokens=settings.llm.max_tokens,  # pyright: ignore[reportCallIssue]
        model_kwargs={
            "cache_control": {"type": "ephemeral"}
        },  # cache the growing conversation
    )
    with ExitStack() as stack:
        queue = open_queue(settings, stack)
        async with Client(CONTENT_SERVER) as client:
            agent = build_cartographer(model, await load_tools(client), system_prompt())

            async def map_source(message: HumanMessage) -> dict:
                start: CartographerState = {
                    "messages": [message],
                    "steps": 0,
                    "stopped": None,
                    "created": [],
                    "deferred": [],
                }
                return await agent.ainvoke(start)

            return await run_once(queue, vault, map_source, commit=commit)


def summary(results: list[ItemResult]) -> str:
    if not results:
        return "Nothing queued."
    counts = Counter(r.outcome for r in results)
    lines = [", ".join(f"{n} {outcome}" for outcome, n in counts.items())]
    for r in results:
        if r.outcome != "written":
            lines.append(f"  {r.outcome}: {r.url}  {r.detail}".rstrip())
            lines += [f"    waiting for you to close: {note}" for note in r.waiting_for]
    return "\n".join(lines)
