"""11.1: one pass of the laptop runner. Pull → Source Note → Cartographer → commit → ack.

Safe to run again after a crash at any point: nothing is acked until its Source is
fully in the Vault and committed, and every step before the ack is idempotent.
"""

from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Literal

import mlflow
from langchain_core.messages import HumanMessage
from loguru import logger
from pydantic import BaseModel, ConfigDict

from synthora.cartographer.agent import source_message
from synthora.obsidian.vault import Vault
from synthora.queue import Queue, QueuedSource
from synthora.runner.source_notes import write_source_note

# The Cartographer, as the runner sees it: a Source in, the run's final state out.
MapSource = Callable[[HumanMessage], Awaitable[dict]]
Commit = Callable[[str], object]  # commit every change in the Vault with this message


class ItemResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    url: str
    outcome: Literal["written", "deferred", "incomplete", "failed"]
    note: str | None = None  # the Source Note's path, once written
    waiting_for: list[str] = []  # FR-25: notes you have open; close them and re-run
    detail: str = ""


async def process(
    item: QueuedSource,
    queue: Queue,
    vault: Vault,
    map_source: MapSource,
    commit: Commit,
) -> ItemResult:
    """One Source into the Vault. Acks only when it is completely written."""
    queue.claim(item.id)
    path = write_source_note(vault, item)
    name = PurePosixPath(
        path
    ).stem  # the note name Evidence must cite, maybe with ' (2)'
    d = item.distillation
    state = await map_source(
        source_message(
            title=name,
            url=item.url,
            tldr=d.tldr,
            key_ideas=d.key_ideas,
            text=item.full_text or "",
        )
    )

    # Whatever happened, what was written is real: commit it, so `git revert` can undo it.
    stopped = state["stopped"]
    commit(f"synthora: {name}" + ("" if stopped == "done" else f" ({stopped})"))
    if stopped == "done":
        queue.ack(item.id)
        return ItemResult(id=item.id, url=item.url, outcome="written", note=path)
    if stopped == "deferred":
        return ItemResult(
            id=item.id,
            url=item.url,
            outcome="deferred",
            note=path,
            waiting_for=state["deferred"],
        )
    return ItemResult(
        id=item.id,
        url=item.url,
        outcome="incomplete",
        note=path,
        detail=f"the Cartographer stopped: {stopped}",
    )


async def run_once(
    queue: Queue, vault: Vault, map_source: MapSource, commit: Commit, limit: int = 50
) -> list[ItemResult]:
    """Write everything that's ready, once. A post's links become ready when the post is
    written, so keep asking the queue until it offers nothing new in this run."""
    results: list[ItemResult] = []
    tried: set[int] = set()
    while len(tried) < limit:
        fresh = [i for i in queue.pending(limit) if i.id not in tried]
        if not fresh:
            break
        for item in fresh[: limit - len(tried)]:
            tried.add(item.id)
            try:
                with mlflow.start_span(name="vault_write", span_type="CHAIN") as span:
                    span.set_inputs({"capture_id": item.id, "url": item.url})
                    mlflow.update_current_trace(tags={"capture_id": str(item.id)})
                    result = await process(item, queue, vault, map_source, commit)
                    span.set_outputs(result.model_dump())
            except (
                Exception
            ) as e:  # one bad item mustn't stop the others; it stays unacked
                logger.opt(exception=e).error(f"Capture {item.id} ({item.url}) failed")
                result = ItemResult(
                    id=item.id,
                    url=item.url,
                    outcome="failed",
                    detail=f"{type(e).__name__}: {e}",
                )
            logger.info(f"Capture {item.id}: {result.outcome} {result.note or ''}")
            results.append(result)
    return results
