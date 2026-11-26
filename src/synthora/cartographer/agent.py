from typing import Annotated, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    HumanMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages  # reducer

from synthora.obsidian.skills import load_skill
from synthora.prompts import cartographer
from synthora import tracing
from synthora.obsidian.vault import DEFERRED

MAX_STEPS = 15  # model calls per Source: a ceiling against loops and runaway cost
MAX_SOURCE_CHARS = (
    8000  # the TL;DR and key ideas carry the meaning; the text is for detail
)
MAX_NEW_NODES = (
    5  # FR-15: new Concepts/Entities per Source; links to existing ones are unlimited
)


def system_prompt() -> str:
    """FR-19a: the Cartographer's instructions, then the obsidian-markdown skill as reference."""
    skill = load_skill("obsidian-markdown")
    return f"{cartographer.PROMPT}\n\n<obsidian_markdown_reference>\n{skill}\n</obsidian_markdown_reference>"


class CartographerState(TypedDict):
    # add_messages APPENDS what a node returns, instead of replacing the list
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int  # how many times the model has been called
    stopped: Literal["done", "deferred", "step_limit"] | None
    created: list[str]  # paths of the Nodes created in this run
    deferred: list[str]  # FR-25: notes skipped because you may be editing them


def source_message(
    *, title: str, url: str, tldr: str, key_ideas: list[str], text: str
) -> HumanMessage:
    """One Source, wrapped so it can't close its own tag (as in the Distiller)."""
    body = text[:MAX_SOURCE_CHARS].replace("</source>", "</ source>")
    ideas = "\n".join(f"- {idea}" for idea in key_ideas)
    return HumanMessage(
        "<source>\n"
        f"<title>{title}</title>\n<url>{url}</url>\n"
        f"<tldr>{tldr}</tldr>\n<key_ideas>\n{ideas}\n</key_ideas>\n"
        f"<text>\n{body}\n</text>\n"
        "</source>\n"
        "Map this Source into the Vault. Its Source Note is titled exactly as <title>."
    )


def _refuse(
    call: ToolCall, by_name: dict[str, BaseTool], created: list[str], max_new_nodes: int
) -> str | None:
    """Rules about the RUN, checked before a call reaches the Content Server.

    The server can't enforce these: it sees single calls, not runs.
    """
    if call["name"] not in by_name:  # the model invented a tool: tell it, don't crash
        return f"no tool named {call['name']!r}; use one of {sorted(by_name)}"
    if call["name"] == "create_node" and len(created) >= max_new_nodes:
        return (
            f"refused: this Source already created {len(created)} new Nodes, the maximum "
            f"(FR-15). Link to existing Nodes, or finish."
        )
    return None


def build_cartographer(
    model: BaseChatModel,
    tools: list[BaseTool],
    prompt: str,
    max_steps: int = MAX_STEPS,
    max_new_nodes: int = MAX_NEW_NODES,
):
    by_name = {t.name: t for t in tools}
    model_with_tools = model.bind_tools(tools)  # sends the tool schemas with every call

    async def agent(state: CartographerState) -> dict:
        tracing.link_prompt("cartographer")
        system = SystemMessage(
            content=[
                {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}
            ]
        )
        reply = await model_with_tools.ainvoke([system, *state["messages"]])
        return {"messages": [reply], "steps": state["steps"] + 1}

    async def run_tools(state: CartographerState) -> dict:
        """Run the requested calls ONE AT A TIME: two writes in parallel could both
        pass find_node and create the same Node twice."""
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        created = list(state["created"])
        deferred = list(state["deferred"])
        results: list[ToolMessage] = []
        for call in last.tool_calls:
            refusal = _refuse(call, by_name, created, max_new_nodes)
            if refusal:
                results.append(
                    ToolMessage(refusal, tool_call_id=call["id"], status="error")
                )
                continue
            result = await by_name[call["name"]].ainvoke(
                call
            )  # a tool_call in → a ToolMessage out
            if call["name"] == "create_node" and result.status == "success":
                created.append(str(result.content))
            results.append(result)
            if result.status == "error" and DEFERRED in str(result.content):
                deferred.append(call["args"].get("path", "?"))
            results.append(result)
        return {"messages": results, "created": created, "deferred": deferred}

    def give_up(state: CartographerState) -> dict:
        return {"stopped": "step_limit"}

    def finish(state: CartographerState) -> dict:
        # Deferred writes mean the Source isn't fully mapped: the runner retries it later.
        return {"stopped": "deferred" if state["deferred"] else "done"}

    def after_agent(state: CartographerState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return "finish"
        if state["steps"] >= max_steps:
            return "give_up"
        return "tools"

    graph = StateGraph(CartographerState)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_node("give_up", give_up)
    graph.add_node("finish", finish)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "give_up", "finish"])
    graph.add_edge("tools", "agent")
    graph.add_edge("give_up", END)
    graph.add_edge("finish", END)
    return graph.compile()
