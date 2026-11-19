"""Tests for the server graph. No network, no API: read and distill are faked."""

import asyncio

from synthora.server.schemas import Capture, Distillation, ExtractedSource, SourceType
from synthora.server.distiller import DistillError
from synthora.server.extraction import ExtractionError
from synthora.agent.graph import GraphDeps, build_graph

SOURCE = ExtractedSource(
    tool="extract_article",
    url="https://stephango.com/saw",
    title="Use the saw, fear the saw",
    text="Respect the tool.",
)
DISTILLATION = Distillation(
    tldr="Powerful tools help and hurt, so respect them.",
    key_ideas=["A tool strong enough to help is strong enough to hurt."],
    notable_quotes=[],
)


async def good_read(url: str, source_type: SourceType) -> ExtractedSource:
    return SOURCE


async def good_distill(source: ExtractedSource) -> Distillation:
    return DISTILLATION


async def broken_read(url: str, source_type: SourceType) -> ExtractedSource:
    raise ExtractionError("HTTP 404")


async def broken_distill(source: ExtractedSource) -> Distillation:
    raise DistillError("Claude declined to distill this source")


def run(capture: Capture, read=good_read, distill=good_distill) -> dict:
    graph = build_graph()
    return asyncio.run(
        graph.ainvoke(
            {"capture": capture}, context=GraphDeps(read=read, distill=distill)
        )
    )


URL = Capture(kind="url", value="https://stephango.com/saw")


def test_happy_path_ends_with_a_tldr_reply():
    state = run(URL)
    assert state["source_type"] is SourceType.WEB_ARTICLE
    assert "error" not in state
    assert DISTILLATION.tldr in state["reply_text"]


def test_read_failure_skips_distill_and_explains_why():
    distill_calls = []

    async def spy_distill(source):
        distill_calls.append(source)
        return DISTILLATION

    state = run(URL, read=broken_read, distill=spy_distill)
    assert distill_calls == []  # the conditional edge went to `failed`, not `distill`
    assert "HTTP 404" in state["reply_text"]


def test_distill_failure_explains_why():
    state = run(URL, distill=broken_distill)
    assert "declined" in state["reply_text"]
    assert "distillation" not in state


def test_image_fails_before_reading():
    state = run(Capture(kind="image", value="file-id-123"), read=broken_read)
    assert state["source_type"] is SourceType.IMAGE
    assert "image" in state["reply_text"]
    assert "source" not in state
