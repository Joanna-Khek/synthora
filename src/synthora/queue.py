"""The laptop's view of the queue (11.1): what to write to the Vault, and acks.

The runner talks to the `Queue` protocol only. `LocalQueue` reads the bot's SQLite
file directly (the bot runs on this laptop for now); after M8 an `HttpQueue` will
call the VPS's pull API (8.4) instead, and the runner won't change.
"""

import sqlite3
import httpx
from datetime import UTC, date, tzinfo
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from synthora import store
from synthora.domain import Distillation, SourceType


class QueueError(Exception):
    """The queue refused a step, e.g. an ack for an item that was never claimed."""


class QueuedSource(BaseModel):
    """One Source waiting for the Vault: everything a Source Note and the Cartographer need.

    The same shape the pull API (8.4) will send as JSON.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    url: str
    source_type: SourceType | None
    title: str | None
    captured: date
    comment: str | None
    distillation: Distillation
    full_text: str | None
    parent_title: str | None  # the post it was linked from, for saved_via


class Queue(Protocol):
    def pending(self, limit: int = 50) -> list[QueuedSource]:
        """What to write next, oldest first. Includes items claimed by a run that didn't finish."""
        ...

    def claim(self, item_id: int) -> None:
        """Mark an item as being written (queued → pulled). Claiming it again is harmless."""
        ...

    def ack(self, item_id: int) -> None:
        """The item is in the Vault (pulled → written). Acking it again is harmless."""
        ...


def _distillation(row: sqlite3.Row) -> Distillation:
    """The stored Distillation; for a Capture from before migration v2, just its TL;DR."""
    stored = store.stored_distillation(row)
    return stored or Distillation(
        tldr=row["tldr"] or "", key_ideas=[], notable_quotes=[]
    )


def _to_source(row: sqlite3.Row, tz: tzinfo) -> QueuedSource:
    return QueuedSource(
        id=row["id"],
        url=row["value"],
        distillation=_distillation(row),
        source_type=SourceType(row["source_type"]) if row["source_type"] else None,
        title=row["title"],
        captured=store.local_time(row["created_at"], tz).date(),
        comment=row["comment"],
        full_text=row["full_text"],
        parent_title=row["parent_title"],
    )


class LocalQueue:
    def __init__(self, conn: sqlite3.Connection, tz: tzinfo = UTC) -> None:
        self._conn = conn
        self._tz = tz  # decides each Source's `captured` date

    def pending(self, limit: int = 50) -> list[QueuedSource]:
        return [
            _to_source(row, self._tz)
            for row in store.ready_for_vault(self._conn, limit)
        ]

    def claim(self, item_id: int) -> None:
        store.mark_pulled(
            self._conn, item_id
        )  # False if already pulled: fine on a re-run

    def ack(self, item_id: int) -> None:
        if not store.mark_written(self._conn, item_id):
            raise QueueError(f"Capture {item_id} can't be acked: it was never claimed")


class HttpQueue:
    """The pull API (8.4) on Fly, for when the bot runs there. Same contract as LocalQueue."""

    def __init__(self, client: httpx.Client) -> None:
        # base_url and the bearer token are set on the client, so tests can pass
        # FastAPI's TestClient (an httpx.Client) and run against the real API.
        self._client = client

    def pending(self, limit: int = 50) -> list[QueuedSource]:
        response = self._client.get("/queue", params={"limit": limit})
        response.raise_for_status()
        return [QueuedSource.model_validate(item) for item in response.json()]

    def claim(self, item_id: int) -> None:
        self._client.post(f"/claim/{item_id}").raise_for_status()

    def ack(self, item_id: int) -> None:
        response = self._client.post(f"/ack/{item_id}")
        if response.status_code == httpx.codes.CONFLICT:  # the API's QueueError
            raise QueueError(response.json()["detail"])
        response.raise_for_status()
