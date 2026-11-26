"""The loop, driven by a scripted fake model: no API calls, fully deterministic."""

import asyncio
import itertools

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from mcp.client import Client

from synthora.cartographer.agent import build_cartographer, CartographerState
from synthora.content import mcp_server
from synthora.mcp_tools import load_tools
from synthora.obsidian.notes import NodeNote
from synthora.obsidian.sections import check_my_notes_untouched

RAG = "Concepts/Retrieval-Augmented Generation.md"


class FakeVault:
    """A dict of notes with one existing Concept (alias RAG). Only what the tools here need."""

    def __init__(self) -> None:
        self.notes = {
            RAG: NodeNote(
                title="Retrieval-Augmented Generation",
                type="concept",
                synthesis="Retrieve, then generate.",
                aliases=["RAG"],
            ).render()
        }

    def exists(self, path: str) -> bool:
        return path in self.notes

    def aliases(self) -> dict[str, str]:
        return {"RAG": RAG}

    def create(self, path: str, body: str) -> None:
        if path in self.notes:
            raise FileExistsError(path)
        self.notes[path] = body

    def edit(self, path: str, change) -> None:
        before = self.notes[path]
        after = change(before)
        check_my_notes_untouched(before, after)
        self.notes[path] = after

    def list_notes(self, folder: str) -> list[str]:
        return sorted(p for p in self.notes if p.startswith(folder + "/"))


PROMPT = "You map Sources into the Vault."
SOURCE = HumanMessage(
    "Source: 'Vector DBs for RAG'. TL;DR: vector databases store embeddings for RAG."
)


class ScriptedModel(GenericFakeChatModel):
    """Replies with the next scripted message. bind_tools is a no-op: the script decides."""

    def bind_tools(self, tools, **kwargs):
        return self


def ask(tool: str, n: int, /, **args) -> AIMessage:
    """A scripted reply asking for one tool call. `/`: tool args may be called `name` too."""
    return AIMessage("", tool_calls=[{"name": tool, "args": args, "id": f"call_{n}"}])


HAPPY_PATH = [
    ask("find_node", 1, name="RAG"),  # exists → link to it
    ask("find_node", 2, name="Vector Database"),  # doesn't exist → create it
    ask(
        "create_node",
        3,
        title="Vector Database",
        type="concept",
        synthesis="Stores embeddings for similarity search.",
        relations={"part_of": ["Retrieval-Augmented Generation"]},
    ),
    ask(
        "append_evidence",
        4,
        path="Concepts/Vector Database.md",
        source="Vector DBs for RAG",
        text="Used as the retrieval store in RAG.",
    ),
    AIMessage("Done: linked RAG, created Vector Database."),
]


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(mcp_server, "open_vault", lambda: fake)
    return fake


def run(build, script, **invoke_kwargs):
    async def go():
        async with Client(mcp_server.mcp) as client:
            tools = await load_tools(client)
            agent = build(ScriptedModel(messages=iter(script)), tools, PROMPT)
            return await agent.ainvoke(invoke_kwargs.pop("state"), **invoke_kwargs)

    return asyncio.run(go())


def start() -> CartographerState:
    return {
        "messages": [SOURCE],
        "steps": 0,
        "stopped": None,
        "created": [],
        "deferred": [],
    }


# ---------------------------------------------------------------- hand-built loop
def test_loop_runs_tools_until_the_model_stops(vault):
    state = run(build_cartographer, HAPPY_PATH, state=start())
    assert state["stopped"] == "done"
    assert state["steps"] == 5  # 4 tool-calling turns + the final answer
    assert state["created"] == ["Concepts/Vector Database.md"]
    assert "Concepts/Vector Database.md" in vault.notes
    assert "Used as the retrieval store" in vault.notes["Concepts/Vector Database.md"]


def test_a_refused_call_is_fed_back_and_the_agent_carries_on(vault):
    script = [
        ask("create_node", 1, title="RAG", type="concept", synthesis="x"),  # duplicate!
        AIMessage("RAG already exists; I'll link to it instead."),
    ]
    state = run(build_cartographer, script, state=start())
    refusal = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert refusal.status == "error" and "link to it instead" in refusal.content
    assert state["stopped"] == "done"
    assert list(vault.notes) == [RAG]  # nothing new was written


def test_an_invented_tool_is_an_error_message_not_a_crash():
    script = [ask("delete_vault", 1), AIMessage("Sorry.")]
    state = run(build_cartographer, script, state=start())
    tool_message = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert "no tool named 'delete_vault'" in tool_message.content


def test_step_limit_stops_a_model_that_never_stops():
    forever = (ask("find_node", n, name="RAG") for n in itertools.count())

    async def go():
        async with Client(mcp_server.mcp) as client:
            agent = build_cartographer(
                ScriptedModel(messages=forever),
                await load_tools(client),
                PROMPT,
                max_steps=3,
            )
            return await agent.ainvoke(start())

    state = asyncio.run(go())
    assert state["stopped"] == "step_limit"
    assert state["steps"] == 3


# ---------------------------------------------------------------- 10.3 node cap
def create(n: int, title: str) -> AIMessage:
    return ask(
        "create_node", n, title=title, type="concept", synthesis=f"About {title}."
    )


def run_capped(script, max_new_nodes):
    async def go():
        async with Client(mcp_server.mcp) as client:
            agent = build_cartographer(
                ScriptedModel(messages=iter(script)),
                await load_tools(client),
                PROMPT,
                max_new_nodes=max_new_nodes,
            )
            return await agent.ainvoke(start())

    return asyncio.run(go())


def test_creates_beyond_the_cap_are_refused_before_reaching_the_vault(vault):
    script = [
        create(1, "Alpha"),
        create(2, "Beta"),
        create(3, "Gamma"),
        AIMessage("Done."),
    ]
    state = run_capped(script, max_new_nodes=2)
    assert state["created"] == ["Concepts/Alpha.md", "Concepts/Beta.md"]
    assert "Concepts/Gamma.md" not in vault.notes
    refusal = [m for m in state["messages"] if isinstance(m, ToolMessage)][-1]
    assert refusal.status == "error" and "maximum (FR-15)" in refusal.content


def test_the_cap_counts_per_call_even_within_one_turn(vault):
    many = AIMessage(
        "",
        tool_calls=[
            {
                "name": "create_node",
                "id": f"c{i}",
                "args": {"title": t, "type": "concept", "synthesis": "x"},
            }
            for i, t in enumerate(["Alpha", "Beta", "Gamma"])
        ],
    )
    state = run_capped([many, AIMessage("Done.")], max_new_nodes=2)
    assert state["created"] == ["Concepts/Alpha.md", "Concepts/Beta.md"]
    assert "Concepts/Gamma.md" not in vault.notes


def test_refused_duplicates_and_links_do_not_count(vault):
    script = [
        create(1, "RAG"),  # duplicate → refused by the server, not counted
        ask("append_evidence", 2, path=RAG, source="S", text="Linked."),  # unlimited
        create(3, "Alpha"),
        AIMessage("Done."),
    ]
    state = run_capped(script, max_new_nodes=1)
    assert state["created"] == ["Concepts/Alpha.md"]
