"""The capture graph: one Capture in, one reply text out.

START → classify → read ─┬─ LinkedIn post → pick_links → read_linked ─┐
                         └─ other source ─────────────────────────────┴→ distill → reply → END
(any "error" goes to failed → END)
"""

from langgraph.graph import END, START, StateGraph

from synthora.agent.nodes import (
    classify,
    distill,
    failed,
    pick_links,
    read,
    read_linked,
    reply,
)
from synthora.agent.state import CaptureState
from synthora.agent.tools import Tools
from synthora.server.schemas import SourceType


# ---------------------------------------------------------------- routing
def ok_or_failed(state: CaptureState) -> str:
    """Router: "error" if the last step failed, otherwise "ok"."""
    return "error" if "error" in state else "ok"


def after_read(state: CaptureState) -> str:
    """Router: a rule, not the LLM. Only a LinkedIn post has links worth picking."""
    if "error" in state:
        return "error"
    if state["source_type"] is SourceType.LINKEDIN_POST:
        return "LinkedIn post"
    return "other source"


# ---------------------------------------------------------------- build
def build_capture_graph():
    builder = StateGraph(CaptureState, context_schema=Tools)

    builder.add_node("classify", classify)
    builder.add_node("read", read)
    builder.add_node("pick_links", pick_links)
    builder.add_node("read_linked", read_linked)
    builder.add_node("distill", distill)
    builder.add_node("reply", reply)
    builder.add_node("failed", failed)

    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify", ok_or_failed, {"ok": "read", "error": "failed"}
    )
    builder.add_conditional_edges(
        "read",
        after_read,
        {"LinkedIn post": "pick_links", "other source": "distill", "error": "failed"},
    )
    builder.add_edge("pick_links", "read_linked")
    builder.add_edge("read_linked", "distill")
    builder.add_conditional_edges(
        "distill", ok_or_failed, {"ok": "reply", "error": "failed"}
    )
    builder.add_edge("reply", END)
    builder.add_edge("failed", END)

    return builder.compile()
