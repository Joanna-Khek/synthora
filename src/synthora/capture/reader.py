import asyncio
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from synthora.domain import ExtractedSource, ExtractionError, SourceType

TOOL_FOR_SOURCE_TYPE = {
    SourceType.WEB_ARTICLE: "extract_article",
    SourceType.LINKEDIN_POST: "extract_with_defuddle",
    SourceType.GITHUB_REPO: "read_github_repo",
    SourceType.YOUTUBE_VIDEO: "read_youtube_metadata",
}

EXTRACTION_SERVER = StdioServerParameters(
    command=sys.executable,  # the same Python (and venv) the bot runs in
    args=["-m", "synthora.extraction.mcp_server"],
)


# Client side of the MCP.
async def read_url(
    client: Client, url: str, source_type: SourceType
) -> ExtractedSource:
    """Call the one tool for this Source Type. Raises ExtractionError if the tool fails."""
    tool = TOOL_FOR_SOURCE_TYPE[source_type]
    result = await client.call_tool(tool, {"url": url})
    if result.is_error:
        message = result.content[0].text if result.content else "unknown error"
        raise ExtractionError(message.removeprefix(f"Error executing tool {tool}: "))
    return ExtractedSource.model_validate(result.structured_content)


async def resolve_url(client: Client, url: str) -> str:
    """Ask the Extraction Server where a lnkd.in short link really points.
    Raises ExtractionError if the tool fails."""
    tool = "resolve_link"
    result = await client.call_tool(tool, {"url": url})
    if result.is_error:
        message = result.content[0].text if result.content else "unknown error"
        raise ExtractionError(message.removeprefix(f"Error executing tool {tool}: "))
    return result.structured_content["result"]


class ExtractionConnection:
    """Keeps one Extraction Server running for as long as the bot runs.

    anyio (used by the MCP client) requires `async with Client(...)` to be
    entered and exited by the SAME task, but PTB runs post_init and
    post_shutdown as different tasks. So one background task owns the
    connection: it opens it, waits for the stop signal, then closes it.
    """

    def __init__(self, params: StdioServerParameters = EXTRACTION_SERVER) -> None:
        self._params = params
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.client: Client | None = None

    async def _hold(self) -> None:
        try:
            async with Client(self._params) as client:
                self.client = client
                self._ready.set()
                await self._stop.wait()
        finally:
            self.client = None
            self._ready.set()  # never leave start() waiting, even on failure

    async def start(self) -> None:
        self._task = asyncio.create_task(self._hold())
        await self._ready.wait()
        if self._task.done():  # the server failed to start
            self._task.result()  # re-raises the error

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
