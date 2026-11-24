from typing import NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict

from synthora.domain import Capture, Distillation, ExtractedSource, SourceType


# One link found inside a post moves through resolve → read → distill, and is
# always in exactly one of these three states. A failure never fails the post.
class LinkedRead(BaseModel):
    """Read, waiting to be distilled (only ever seen between read_linked and distill)."""

    model_config = ConfigDict(frozen=True)

    url: str  # after resolving a short link
    source_type: SourceType
    source: ExtractedSource


class LinkedDistilled(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    source_type: SourceType
    source: ExtractedSource
    distillation: Distillation


class LinkedFailed(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    error: str  # why this one link failed
    source_type: SourceType | None = None  # None if it failed before being classified


LinkedSource = LinkedRead | LinkedDistilled | LinkedFailed


class CaptureState(TypedDict):
    """Everything the graph knows about one Capture, filled in step by step.

    Only `capture` is given at the start; each node adds its own keys.
    """

    capture: Capture  # the link you sent
    source_type: NotRequired[SourceType]  # classify
    source: NotRequired[ExtractedSource]  # read
    linked_urls: NotRequired[list[str]]  # pick_links: validated picks, in order
    notes: NotRequired[list[str]]  # pick_links: e.g. "hit the safety ceiling"
    linked: NotRequired[list[LinkedSource]]  # read_linked, then distill
    distillation: NotRequired[Distillation]  # distill
    error: NotRequired[str]  # why the link you sent failed
