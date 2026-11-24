"""Tests for the agent package: link finding, link picking and the capture graph.

No network, no MCP server, no API calls: every outside dependency is faked
by FakeTools (see fakes.py), which satisfies the Tools protocol.
"""

import asyncio

import pytest
from fakes import (
    ARTICLE_URL,
    POST,
    POST_TEXT,
    POST_URL,
    BrokenDistillTools,
    BrokenReadTools,
    FakeTools,
    redirect,
)
from langchain_core.runnables import RunnableLambda

from synthora.capture.distiller import DistillError
from synthora.capture.graph import build_capture_graph
from synthora.capture.link_picker import LinkPicks, LinkSelection, pick_links
from synthora.capture.links import number_links, unwrap_linkedin_redirect
from synthora.capture.nodes import is_short_link
from synthora.capture.tools import CaptureContext, Tools
from synthora.domain import (
    Capture,
    ExtractionError,
    SourceType,
)


def run(tools: Tools, value: str = ARTICLE_URL, kind: str = "url") -> dict:
    graph = build_capture_graph()
    return asyncio.run(
        graph.ainvoke(
            {"capture": Capture(kind=kind, value=value)},
            context=CaptureContext(tools=tools),
        )
    )


# ---------------------------------------------------------------- links.py
def test_unwrap_linkedin_redirect_returns_the_real_link():
    assert (
        unwrap_linkedin_redirect(redirect("https://lnkd.in/gThZcnms"))
        == "https://lnkd.in/gThZcnms"
    )


def test_unwrap_leaves_other_urls_alone():
    assert (
        unwrap_linkedin_redirect("https://github.com/a/b") == "https://github.com/a/b"
    )


def test_number_links_numbers_each_unique_link_once_and_skips_images():
    text, links = number_links(POST_TEXT)
    assert [link.url for link in links] == [
        "https://lnkd.in/gThZcnms",
        "https://lnkd.in/g9q7854R",
        "http://GetFluently.app",
    ]
    assert [link.id for link in links] == [1, 2, 3]
    assert text.count("[link 1]") == 2  # the repeated Strix link keeps its number
    assert (
        "GetFluently.app [link 3]" in text
    )  # the label stays, so the LLM sees what it is
    assert "redir/redirect" not in text  # the LLM never sees the noisy wrapper
    assert "pic.png" not in text  # images are dropped


# ---------------------------------------------------------------- link_picker.py
def llm_answering(link_ids: list[int], seen: dict | None = None) -> RunnableLambda:
    """A fake picker model that always answers with these link numbers."""

    def answer(messages):
        if seen is not None:
            seen["human"] = messages[1][1]
        return LinkPicks(reasoning="Fake.", link_ids=link_ids)

    return RunnableLambda(answer)


def pick(link_ids: list[int], ceiling: int = 25) -> LinkSelection:
    return asyncio.run(pick_links(llm_answering(link_ids), POST, ceiling))


def test_pick_links_shows_the_model_numbered_links_inside_post_tags():
    seen = {}
    selection = asyncio.run(pick_links(llm_answering([1, 2], seen), POST, ceiling=25))

    assert [link.id for link in selection.chosen] == [1, 2]
    assert not selection.hit_ceiling
    assert seen["human"].startswith("<post>") and seen["human"].endswith("</post>")
    assert "[link 3]" in seen["human"]


def test_made_up_numbers_and_duplicates_are_dropped():
    selection = pick([2, 99, 1, 2, -1])
    assert [link.id for link in selection.chosen] == [2, 1]  # the LLM's order is kept


def test_picks_stop_at_the_ceiling():
    selection = pick([1, 2, 3], ceiling=2)
    assert [link.id for link in selection.chosen] == [1, 2]
    assert selection.hit_ceiling


def test_exactly_reaching_the_ceiling_is_not_hitting_it():
    assert not pick([1, 2], ceiling=2).hit_ceiling


def test_pick_links_skips_the_llm_when_the_post_has_no_links():
    def must_not_be_called(messages):
        raise AssertionError("the LLM was called for a post without links")

    no_links = POST.model_copy(update={"text": "Just an opinion, no links."})
    selection = asyncio.run(
        pick_links(RunnableLambda(must_not_be_called), no_links, ceiling=25)
    )
    assert selection == LinkSelection(chosen=[], hit_ceiling=False)


# ---------------------------------------------------------------- graph: one ordinary link
def test_article_happy_path_ends_with_a_tldr():
    tools = FakeTools()
    state = run(tools)
    assert state["source_type"] is SourceType.WEB_ARTICLE
    assert "error" not in state
    assert "pick_links" not in tools.calls  # only LinkedIn posts have links to pick
    assert "linked" not in state
    assert state["distillation"].tldr == f"TL;DR of {ARTICLE_URL}"


def test_read_failure_skips_distill():
    tools = BrokenReadTools()
    state = run(tools)
    assert not any(call.startswith("distill") for call in tools.calls)
    assert "HTTP 404" in state["error"]


def test_distill_failure_explains_why():
    state = run(BrokenDistillTools())
    assert "declined" in state["error"]
    assert "distillation" not in state


def test_image_fails_before_reading():
    tools = FakeTools()
    state = run(tools, value="telegram-file-id", kind="image")
    assert tools.calls == []
    assert "image" in state["error"]


# ---------------------------------------------------------------- graph: a LinkedIn post
@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://lnkd.in/gThZcnms", True),
        (
            "https://lnkd.in/p/g2X4FX6S",
            False,
        ),  # a post share link, handled by the Source Type rules
        ("https://github.com/a/b", False),
    ],
)
def test_is_short_link(url, expected):
    assert is_short_link(url) is expected


def test_post_reads_and_picks_links_before_distilling_anything():
    tools = FakeTools(picks=[1, 2])  # the two repos, not the self-promotion (3)
    run(tools, value=POST_URL)

    first_distill = next(
        i for i, call in enumerate(tools.calls) if call.startswith("distill")
    )
    assert tools.calls[0] == f"read {POST_URL}"  # the link you sent is read first
    assert tools.calls.index("pick_links") < first_distill
    last_linked_read = max(
        i
        for i, call in enumerate(tools.calls)
        if call.startswith("read https://github.com")
    )
    assert (
        last_linked_read < first_distill
    )  # every link is read before any distilling starts


def test_post_distills_the_post_and_each_linked_resource():
    tools = FakeTools(picks=[1, 2])
    state = run(tools, value=POST_URL)

    assert state["linked_urls"] == [
        "https://lnkd.in/gThZcnms",
        "https://lnkd.in/g9q7854R",
    ]
    assert [
        item["url"] for item in state["linked"]
    ] == [  # resolved, then read by the rules
        "https://github.com/example/gThZcnms",
        "https://github.com/example/g9q7854R",
    ]
    assert all(
        item["source_type"] is SourceType.GITHUB_REPO for item in state["linked"]
    )
    assert state["distillation"].tldr == f"TL;DR of {POST_URL}"
    assert [item["distillation"].tldr for item in state["linked"]] == [
        "TL;DR of https://github.com/example/gThZcnms",
        "TL;DR of https://github.com/example/g9q7854R",
    ]
    assert (
        sum(call.startswith("distill") for call in tools.calls) == 3
    )  # one call per source
    assert all("error" not in item for item in state["linked"])


def test_made_up_link_numbers_from_the_llm_are_ignored():
    state = run(FakeTools(picks=[1, 99]), value=POST_URL)
    assert state["linked_urls"] == ["https://lnkd.in/gThZcnms"]


def test_a_failed_short_link_fails_only_that_link():
    class OneBadResolve(FakeTools):
        async def resolve(self, url):
            if url.endswith("g9q7854R"):
                raise ExtractionError("no external link found on the lnkd.in page")
            return await super().resolve(url)

    state = run(OneBadResolve(), value=POST_URL)
    assert "error" not in state
    assert "error" not in state["linked"][0]
    assert "no external link found" in state["linked"][1]["error"]


def test_a_failed_linked_distill_fails_only_that_link():
    class OneBadDistill(FakeTools):
        async def distill(self, source):
            if source.url.endswith("gThZcnms"):
                raise DistillError("the answer was cut off")
            return await super().distill(source)

    state = run(OneBadDistill(), value=POST_URL)
    assert "error" not in state  # the post itself is fine
    assert "cut off" in state["linked"][0]["error"]
    assert "distillation" in state["linked"][1]


def test_a_linked_resource_that_failed_to_read_is_not_distilled():
    class OneBadRead(FakeTools):
        async def read(self, url, source_type):
            if url.endswith("g9q7854R"):
                raise ExtractionError("HTTP 404")
            return await super().read(url, source_type)

    tools = OneBadRead()
    state = run(tools, value=POST_URL)
    assert "distill https://github.com/example/g9q7854R" not in tools.calls
    assert "HTTP 404" in state["linked"][1]["error"]


def test_failed_post_does_not_pick_links():
    tools = BrokenReadTools()
    state = run(tools, value=POST_URL)
    assert "pick_links" not in tools.calls
    assert "HTTP 404" in state["error"]


def test_safety_ceiling_is_reported():
    class LowCeiling(FakeTools):
        max_linked = 1

    state = run(LowCeiling(picks=[1, 2]), value=POST_URL)
    assert len(state["linked_urls"]) == 1
    assert "safety ceiling of 1" in state["notes"][0]


def test_at_most_max_concurrent_links_are_read_at_once():
    text = " ".join(
        f"[r{i}](https://example.com/{i})" for i in range(1, 6)
    )  # five links
    post = POST.model_copy(update={"text": text})

    class Counting(FakeTools):
        max_concurrent = 2
        running = 0
        peak = 0

        async def read(self, url, source_type):
            if source_type is SourceType.LINKEDIN_POST:
                return post
            Counting.running += 1
            Counting.peak = max(Counting.peak, Counting.running)
            await asyncio.sleep(0.01)
            Counting.running -= 1
            return await super().read(url, source_type)

    run(Counting(picks=[1, 2, 3, 4, 5]), value=POST_URL)
    assert Counting.peak == 2
