"""The capture pipeline: one Capture in, one CaptureOutcome out.

Owns everything that happens to a Capture apart from talking to Telegram:
the Repeat Capture check, the database row's lifecycle, the graph run and
its MLflow span. The bot only parses messages and renders the outcome.
"""

import sqlite3
from collections.abc import Awaitable, Callable
from typing import cast

import mlflow
from pydantic import BaseModel, ConfigDict

from synthora import store
from synthora.capture.distiller import DistillError
from synthora.capture.graph import build_capture_graph
from synthora.capture.state import CaptureState, LinkedSource
from synthora.capture.tools import CaptureContext, Tools
from synthora.domain import Capture, Distillation, ExtractedSource, SourceType


class MessageIds(BaseModel):
    """Where a Capture came from in Telegram, so a reply to its failure can find it."""

    model_config = ConfigDict(frozen=True)

    chat_id: int
    user_message_id: int  # the message the owner sent
    reply_message_id: int  # the bot's "Processing..." message


# ---------------------------------------------------------------- outcomes
class Repeat(BaseModel):
    """The URL was already captured successfully (FR-23); nothing was run."""

    model_config = ConfigDict(frozen=True)

    captured_on: str  # YYYY-MM-DD
    tldr: str


class Succeeded(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: ExtractedSource
    distillation: Distillation
    linked: list[LinkedSource] = []
    notes: list[str] = []  # e.g. "hit the safety ceiling"


class Failed(BaseModel):
    """A Failed Capture: the owner is told why and may reply to complete it (FR-22)."""

    model_config = ConfigDict(frozen=True)

    reason: str
    source_type: SourceType | None = None


CaptureOutcome = Repeat | Succeeded | Failed


# ---------------------------------------------------------------- progress
class StepDone(BaseModel):
    """A graph step finished; `update` holds only the state keys it changed."""

    model_config = ConfigDict(frozen=True)

    node: str
    update: dict


class StepCount(BaseModel):
    """Partway through a step that handles several sources, e.g. 2 of 5 links read."""

    model_config = ConfigDict(frozen=True)

    step: str
    done: int
    total: int


ProgressEvent = StepDone | StepCount
OnProgress = Callable[[ProgressEvent], Awaitable[None]]


async def _ignore_progress(event: ProgressEvent) -> None:
    pass


# ---------------------------------------------------------------- pipeline
class CapturePipeline:
    def __init__(self, db: sqlite3.Connection, tools: Tools) -> None:
        self._db = db
        self._tools = tools
        self._graph = build_capture_graph()

    async def run(
        self,
        capture: Capture,
        ids: MessageIds,
        on_progress: OnProgress = _ignore_progress,
    ) -> CaptureOutcome:
        """Process one Capture and record the result.

        The database row always ends as 'queued' or 'failed', never stuck in
        'processing': an unexpected exception marks it failed, then re-raises.
        """
        if capture.kind == "url" and (
            previous := store.find_successful(self._db, capture.value)
        ):
            return Repeat(
                captured_on=previous["created_at"][:10], tldr=previous["tldr"]
            )

        capture_id = store.add_capture(
            self._db,
            capture,
            chat_id=ids.chat_id,
            user_message_id=ids.user_message_id,
            reply_message_id=ids.reply_message_id,
        )
        with mlflow.start_span(name="capture", span_type="CHAIN") as span:
            span.set_inputs(
                {"url": capture.value, "kind": capture.kind, "comment": capture.comment}
            )
            mlflow.update_current_trace(tags={"capture_id": str(capture_id)})

            state: dict = {}
            try:
                await self._run_graph(capture, state, on_progress)
            except Exception as e:  # a bug: don't leave the row stuck in 'processing'
                store.mark_failed(
                    self._db,
                    capture_id,
                    reason=f"bug: {type(e).__name__}",
                    source_type=state.get("source_type"),
                )
                raise

            outcome = _outcome(state)
            span.set_outputs({"error": state.get("error")})
            mlflow.update_current_trace(
                tags={"outcome": "failed" if isinstance(outcome, Failed) else "queued"}
            )

        if isinstance(outcome, Failed):
            store.mark_failed(
                self._db,
                capture_id,
                reason=outcome.reason,
                source_type=outcome.source_type,
            )
        else:
            store.mark_queued(
                self._db,
                capture_id,
                source_type=state["source_type"],
                tldr=outcome.distillation.tldr,
                full_text=outcome.source.text,
            )
        return outcome

    async def complete(self, failed: sqlite3.Row, text: str) -> Succeeded | Failed:
        """FR-22: complete a Failed Capture from text the owner pasted in reply."""
        source = ExtractedSource(tool="pasted_by_user", url=failed["value"], text=text)
        try:
            distillation = await self._tools.distill(source)
        except DistillError as e:
            return Failed(reason=f"couldn't distill it: {e}")

        if not store.complete_failed(
            self._db, failed["id"], tldr=distillation.tldr, full_text=text
        ):
            return Failed(reason="it was already completed")
        return Succeeded(source=source, distillation=distillation)

    async def _run_graph(
        self, capture: Capture, state: dict, on_progress: OnProgress
    ) -> None:
        """Stream the graph, merging each step's changes into `state` as they arrive."""
        initial: CaptureState = {"capture": capture}
        async for mode, chunk in self._graph.astream(
            initial,
            context=CaptureContext(tools=self._tools),
            stream_mode=["updates", "custom"],
        ):
            # With several stream modes, LangGraph types each chunk loosely; both of
            # ours are dicts: {node: update} for "updates", the node's dict for "custom".
            data = cast(dict, chunk)
            if mode == "updates":
                for node, update in data.items():
                    state |= update or {}
                    await on_progress(StepDone(node=node, update=update or {}))
            else:  # "custom": a counter written by read_linked or distill
                await on_progress(
                    StepCount(step=data["step"], done=data["done"], total=data["total"])
                )


def _outcome(state: dict) -> Succeeded | Failed:
    if "error" in state:
        return Failed(reason=state["error"], source_type=state.get("source_type"))
    return Succeeded(
        source=state["source"],
        distillation=state["distillation"],
        linked=state.get("linked", []),
        notes=state.get("notes", []),
    )
