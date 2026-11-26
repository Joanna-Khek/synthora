"""MCP tools as LangChain tools

We write it ourselves because langchain-mcp-adapters 0.3.2 requires mcp<2, and both
our servers use mcp 2.x.
"""

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp.client import Client


def _to_langchain(
    client: Client, name: str, description: str, schema: dict
) -> BaseTool:
    async def call(**arguments) -> str:
        result = await client.call_tool(name, arguments)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        if result.is_error:
            raise ToolException(text)  # becomes an error ToolMessage the model can read
        return text

    return StructuredTool(
        name=name,
        description=description,
        args_schema=schema,  # the server's JSON schema, passed through unchanged
        coroutine=call,
        handle_tool_error=True,  # a ToolException is returned to the model, not raised
    )


async def load_tools(client: Client) -> list[BaseTool]:
    """Every tool the connected MCP server offers, ready for model.bind_tools()."""
    listed = await client.list_tools()
    return [
        _to_langchain(client, t.name, t.description or "", t.input_schema)
        for t in listed.tools
    ]
