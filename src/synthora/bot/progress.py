"""Show the graph's progress in Telegram by editing the "Processing..." message."""

import time

from loguru import logger
from telegram import LinkPreviewOptions, Message
from telegram.error import BadRequest, RetryAfter

from synthora.capture.pipeline import ProgressEvent, StepCount

TELEGRAM_MAX_CHARS = 4096

# What each node has done, shown once it finishes. Nodes not listed here are not shown.
STEP_DONE = {
    "classify": "🔎 Classified",
    "read": "📥 Read",
    "pick_links": "🔗 Picked links",
    "read_linked": "📚 Read linked resources",
    "distill": "🧠 Distilled",
}


def describe_update(node: str, update: dict) -> str | None:
    """One checklist line for a node that just finished (or None to show nothing)."""
    if node not in STEP_DONE:
        return None
    line = STEP_DONE[node]
    if node == "classify" and "source_type" in update:
        line += f": {update['source_type']}"
    elif node == "read" and "source" in update:
        line += f": {(update['source'].title or update['source'].url)[:60]}"
    elif node == "pick_links":
        line += f": {len(update['linked_urls'])} to follow"
    if "error" in update:
        line = line.split(":")[0] + " ❌"
    return line


class ProgressLog:
    """The checklist for one Capture: a line per finished step, plus a live counter."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._counter = ""

    def add(self, event: ProgressEvent) -> None:
        if isinstance(event, StepCount):
            self._counter = f"⏳ {event.step}: {event.done}/{event.total}"
            return
        line = describe_update(event.node, event.update)
        if line:
            self._lines.append(line)
            self._counter = ""  # a step finished, so its counter is no longer needed

    def text(self) -> str:
        return "\n".join(self._lines + [self._counter or "⏳ working…"])


class LiveMessage:
    """A Telegram message we keep editing. Edits are throttled, because Telegram
    limits how often one chat can be edited (roughly once a second)."""

    def __init__(self, message: Message, min_interval: float = 1.0) -> None:
        self.message = message
        self.min_interval = min_interval
        self._shown = ""
        self._last_edit = 0.0

    async def show(self, text: str, final: bool = False) -> None:
        text = text[:TELEGRAM_MAX_CHARS]
        if text == self._shown:
            return  # Telegram rejects an edit that changes nothing
        if not final and time.monotonic() - self._last_edit < self.min_interval:
            return  # too soon; a later update (or the final one) will show it
        try:
            await self.message.edit_text(
                text, link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        except RetryAfter as e:
            logger.warning(f"Telegram asked us to slow down for {e.retry_after}s")
            return
        except BadRequest as e:
            logger.warning(f"could not edit the progress message: {e}")
            return
        self._shown = text
        self._last_edit = time.monotonic()
