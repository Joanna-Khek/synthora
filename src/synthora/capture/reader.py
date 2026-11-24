"""The Reader's side of the Extraction Server: one client, started once, used by every Capture."""

import asyncio
import sys
from typing import TYPE_CHECKING, Any, Self

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from synthora.domain import ExtractedSource, ExtractionError, SourceType

if TYPE_CHECKING:  # only for the type hint: the bot never imports the server code
    from mcp.server.mcpserver import MCPServer

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


class ExtractionClient:
    """Keeps one Extraction Server running and reads Sources through it.

    Use it as `async with ExtractionClient() as reader:`, or call start() and
    stop() yourself when they run in different places, as in the bot.

    Why a background task: anyio (used by the MCP client) requires
    `async with Client(...)` to be entered and exited by the SAME task, but PTB
    runs post_init and post_shutdown as different tasks. So one task owns the
    connection: it opens it, waits for the stop signal, then closes it.
    """

    def __init__(
        self, server: "StdioServerParameters | MCPServer" = EXTRACTION_SERVER
    ) -> None:
        # A child process (the bot) or an in-memory server (the tests).
        self._server = server
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._client: Client | None = None

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Launch the server and connect. Raises if the server fails to start."""
        self._task = asyncio.create_task(self._hold())
        await self._ready.wait()
        if self._task.done():  # the server failed to start
            self._task.result()  # re-raises the error

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def _hold(self) -> None:
        try:
            async with Client(self._server) as client:
                self._client = client
                self._ready.set()
                await self._stop.wait()
        finally:
            self._client = None
            self._ready.set()  # never leave start() waiting, even on failure

    # ---------------------------------------------------------------- reading
    async def read(self, url: str, source_type: SourceType) -> ExtractedSource:
        """Call the one tool for this Source Type. Raises ExtractionError if it fails."""
        result = await self._call(TOOL_FOR_SOURCE_TYPE[source_type], url)
        return ExtractedSource.model_validate(result)

    async def resolve(self, url: str) -> str:
        """Where a lnkd.in short link really points. Raises ExtractionError if it fails."""
        return (await self._call("resolve_link", url))["result"]

    async def _call(self, tool: str, url: str) -> dict[str, Any]:
        """Call one tool; a tool failure becomes ExtractionError with the server's reason."""
        if self._client is None:
            raise RuntimeError("ExtractionClient is not running: call start() first")
        result = await self._client.call_tool(tool, {"url": url})
        if result.is_error:
            first = result.content[0] if result.content else None
            message = getattr(first, "text", None) or "unknown error"
            raise ExtractionError(
                message.removeprefix(f"Error executing tool {tool}: ")
            )
        return result.structured_content or {}
