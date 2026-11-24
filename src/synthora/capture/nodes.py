"""Nodes of the capture graph. Each node takes the state and returns ONLY the keys it changes."""

import asyncio

from langgraph.runtime import Runtime
from loguru import logger

from synthora.capture.distiller import DistillError
from synthora.capture.state import CaptureState, LinkedSource
from synthora.capture.tools import CaptureContext, Tools
from synthora.domain import (
    Distillation,
    ExtractedSource,
    ExtractionError,
    SourceType,
    guess_source_type,
    hint_for,
)
from synthora.domain.urls import split_url


# ---------------------------------------------------------------- 1. classify
async def classify(state: CaptureState) -> dict:
    source_type = hint_for(state["capture"])
    if source_type is SourceType.IMAGE:
        return {
            "source_type": source_type,
            "error": "image reading isn't supported yet",
        }
    return {"source_type": source_type}


# ---------------------------------------------------------------- 2. read
async def read(state: CaptureState, runtime: Runtime[CaptureContext]) -> dict:
    try:
        source = await runtime.context.tools.read(
            state["capture"].value, state["source_type"]
        )
    except ExtractionError as e:
        return {"error": f"couldn't read it: {e}"}
    return {"source": source}


# ---------------------------------------------------------------- 3. pick_links (LinkedIn posts only)
async def pick_links(state: CaptureState, runtime: Runtime[CaptureContext]) -> dict:
    """Ask the LLM which links to follow; the picks come back already checked."""
    tools = runtime.context.tools
    selection = await tools.pick_links(state["source"])

    notes = []
    if selection.hit_ceiling:
        logger.warning(
            f"hit the safety ceiling of {tools.max_linked} links for {state['capture'].value}"
        )
        notes.append(f"⚠️ stopped at the safety ceiling of {tools.max_linked} links")
    return {"linked_urls": [link.url for link in selection.chosen], "notes": notes}


# ---------------------------------------------------------------- 4. read_linked (LinkedIn posts only)
def is_short_link(url: str) -> bool:
    """lnkd.in/xyz needs resolving; lnkd.in/p/xyz is a post share link (handled by the rules)."""
    host, path, _ = split_url(url)
    return host == "lnkd.in" and path[:1] != ["p"]


async def read_one_link(url: str, tools: Tools) -> LinkedSource:
    """Resolve (if needed) → pick the extractor by rules → read. Failures become data."""
    if is_short_link(url):
        try:
            url = await tools.resolve(url)
        except ExtractionError as e:
            return {"url": url, "error": f"couldn't resolve the short link: {e}"}

    source_type = guess_source_type(url)  # the same rules as classify
    try:
        source = await tools.read(url, source_type)
    except ExtractionError as e:
        return {
            "url": url,
            "source_type": source_type,
            "error": f"couldn't read it: {e}",
        }
    return {"url": url, "source_type": source_type, "source": source}


async def read_linked(state: CaptureState, runtime: Runtime[CaptureContext]) -> dict:
    """Read every picked link, at most `max_concurrent` at a time."""
    tools = runtime.context.tools
    limit = asyncio.Semaphore(tools.max_concurrent)
    total = len(state["linked_urls"])
    done = 0

    async def one(url: str) -> LinkedSource:
        nonlocal done
        async with limit:
            result = await read_one_link(url, tools)
        done += 1
        runtime.stream_writer({"step": "read_linked", "done": done, "total": total})
        return result

    results = await asyncio.gather(*(one(url) for url in state["linked_urls"]))
    return {"linked": list(results)}


# ---------------------------------------------------------------- 5. distill
async def distill(state: CaptureState, runtime: Runtime[CaptureContext]) -> dict:
    """Distill the source you sent AND every linked source that was read.

    One stage, but one LLM call per source, so each gets its own TL;DR.
    """
    tools = runtime.context.tools
    limit = asyncio.Semaphore(tools.max_concurrent)

    total = 1 + sum("source" in item for item in state.get("linked", []))
    done = 0

    async def distill_one(source: ExtractedSource) -> Distillation | DistillError:
        nonlocal done
        async with limit:
            try:
                result = await tools.distill(source)
            except DistillError as e:
                result = (
                    e  # returned, not raised, so one failure doesn't stop the others
                )
        done += 1
        runtime.stream_writer({"step": "distill", "done": done, "total": total})
        return result

    async def distill_linked(item: LinkedSource) -> LinkedSource:
        if "source" not in item:  # reading it failed earlier: nothing to distill
            return item
        result = await distill_one(item["source"])
        if isinstance(result, DistillError):
            return {**item, "error": f"couldn't distill it: {result}"}
        return {**item, "distillation": result}

    main, *linked = await asyncio.gather(
        distill_one(state["source"]),
        *(distill_linked(item) for item in state.get("linked", [])),
    )

    updates: dict = {"linked": linked} if "linked" in state else {}
    if isinstance(main, DistillError):
        return {**updates, "error": f"couldn't distill it: {main}"}
    return {**updates, "distillation": main}
