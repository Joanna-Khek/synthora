import asyncio

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from mcp.client import Client

from synthora.content import mcp_server
from synthora.mcp_tools import load_tools
from test_content_writes import FakeVault


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(mcp_server, "open_vault", lambda: fake)
    return fake


def with_tools(go):
    async def run():
        async with Client(mcp_server.mcp) as client:
            tools = {t.name: t for t in await load_tools(client)}
            return await go(tools)

    return asyncio.run(run())


def test_every_server_tool_becomes_a_langchain_tool():
    async def go(tools):
        return tools

    tools = with_tools(go)
    assert "create_node" in tools and "find_node" in tools
    schema = convert_to_openai_tool(tools["create_node"])["function"]["parameters"]
    assert {"title", "type", "synthesis"} <= set(schema["required"])
    assert "ctx" not in schema["properties"]  # the server hides its Context parameter


def test_a_call_reaches_the_server(vault):
    async def go(tools):
        return await tools["create_node"].ainvoke(
            {
                "title": "Vector Database",
                "type": "concept",
                "synthesis": "Stores embeddings.",
            }
        )

    assert "Concepts/Vector Database.md" in with_tools(go)
    assert "Concepts/Vector Database.md" in vault.notes


def test_a_refusal_comes_back_as_an_error_message_not_an_exception():
    async def go(tools):
        # invoked with a tool call (as the agent loop does), so we get a ToolMessage back
        return await tools["create_node"].ainvoke(
            {
                "type": "tool_call",
                "id": "1",
                "name": "create_node",
                "args": {"title": "RAG", "type": "concept", "synthesis": "x"},
            }
        )

    message = with_tools(go)
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert "link to it instead" in message.content
