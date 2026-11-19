from dataclasses import dataclass

from langchain_core.runnables import Runnable
from mcp.client import Client

from synthora.agent import link_picker
from synthora.agent.links import PostLink
from synthora.server import distiller, reader
from synthora.server.schemas import Distillation, ExtractedSource, SourceType


@dataclass(
    init=False
)  # only so LangGraph's type hints accept it; we write __init__ ourselves to make it clearer
class Tools:
    """Everything the nodes need from the outside world, in one object."""

    client: Client
    distill_model: Runnable
    picker_model: Runnable

    max_linked = 25  # safety ceiling
    max_concurrent = 4  # links read at the same time

    def __init__(
        self, client: Client, distill_model: Runnable, picker_model: Runnable
    ) -> None:
        self.client = client  # talks to the Extraction Server
        self.distill_model = distill_model  # Claude, for TL;DRs
        self.picker_model = picker_model  # Claude, for picking links

    async def read(self, url: str, source_type: SourceType) -> ExtractedSource:
        return await reader.read_url(self.client, url, source_type)

    async def resolve(self, url: str) -> str:
        return await reader.resolve_url(self.client, url)

    async def distill(self, source: ExtractedSource) -> Distillation:
        return await distiller.distill(self.distill_model, source)

    async def pick_links(
        self, post: ExtractedSource
    ) -> tuple[list[PostLink], list[int]]:
        return await link_picker.pick_links(self.picker_model, post)
