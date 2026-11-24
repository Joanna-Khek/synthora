"""The capture graph: one Capture in, the filled-in CaptureState out.

START → classify → read ─┬─ LinkedIn post → pick_links → read_linked ─┐
                         └─ other source ─────────────────────────────┴→ distill → END
(any "error" goes straight to END; the pipeline turns the state into an outcome)
"""

from langgraph.graph import END, START, StateGraph

from synthora.capture.nodes import (
    classify,
    distill,
    pick_links,
    read,
    read_linked,
)
from synthora.capture.state import CaptureState
from synthora.capture.tools import CaptureContext
from synthora.domain import SourceType


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
    builder = StateGraph(CaptureState, context_schema=CaptureContext)

    builder.add_node("classify", classify)
    builder.add_node("read", read)
    builder.add_node("pick_links", pick_links)
    builder.add_node("read_linked", read_linked)
    builder.add_node("distill", distill)

    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify", ok_or_failed, {"ok": "read", "error": END}
    )
    builder.add_conditional_edges(
        "read",
        after_read,
        {"LinkedIn post": "pick_links", "other source": "distill", "error": END},
    )
    builder.add_edge("pick_links", "read_linked")
    builder.add_edge("read_linked", "distill")
    builder.add_edge("distill", END)

    return builder.compile()
