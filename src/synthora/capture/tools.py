"""What the capture graph needs from the outside world: the seam the tests fake."""

from typing import Protocol, runtime_checkable

import mlflow
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict

from synthora import tracing
from synthora.capture import distiller, link_picker
from synthora.capture.link_picker import LinkSelection
from synthora.capture.reader import ExtractionClient
from synthora.config import CaptureConfig
from synthora.domain import Distillation, ExtractedSource, SourceType


@runtime_checkable  # so pydantic can check CaptureContext.tools
class Tools(Protocol):
    """Everything the nodes call. LiveTools in production, FakeTools in the tests."""

    max_linked: int  # safety ceiling on links followed from one post
    max_concurrent: int  # links read or distilled at the same time

    async def read(self, url: str, source_type: SourceType) -> ExtractedSource: ...

    async def resolve(self, url: str) -> str: ...

    async def distill(self, source: ExtractedSource) -> Distillation: ...

    async def pick_links(self, post: ExtractedSource) -> LinkSelection: ...


class CaptureContext(BaseModel):
    """What LangGraph hands every node as runtime.context.

    LangGraph only accepts a TypedDict, dataclass or pydantic model here, not a
    Protocol, so the Tools travel inside this model.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    tools: Tools


class LiveTools:
    """The real Tools: the Extraction Server for reading, Claude for thinking."""

    def __init__(
        self,
        extraction: ExtractionClient,
        distill_model: Runnable,
        picker_model: Runnable,
        limits: CaptureConfig,
    ) -> None:
        self.extraction = extraction  # the running Extraction Server
        self.distill_model = distill_model  # Claude, for TL;DRs
        self.picker_model = picker_model  # Claude, for picking links
        self.max_linked = limits.max_linked
        self.max_concurrent = limits.max_concurrent
        self.max_source_chars = limits.max_source_chars

    @mlflow.trace(span_type="TOOL")
    async def read(self, url: str, source_type: SourceType) -> ExtractedSource:
        return await self.extraction.read(url, source_type)

    @mlflow.trace(span_type="TOOL")
    async def resolve(self, url: str) -> str:
        return await self.extraction.resolve(url)

    @mlflow.trace(span_type="CHAIN")
    async def distill(self, source: ExtractedSource) -> Distillation:
        tracing.link_prompt("distill")  # name must match filename in /prompts
        return await distiller.distill(
            self.distill_model, source, max_chars=self.max_source_chars
        )

    @mlflow.trace(span_type="CHAIN")
    async def pick_links(self, post: ExtractedSource) -> LinkSelection:
        tracing.link_prompt("picker")  # name must match filename in /prompts
        return await link_picker.pick_links(self.picker_model, post, self.max_linked)
