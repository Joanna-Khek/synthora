from typing import NotRequired, TypedDict

from synthora.domain import Capture, Distillation, ExtractedSource, SourceType


class LinkedSource(TypedDict):
    """One link found inside a post, followed through resolve → read → distill."""

    url: str
    source_type: NotRequired[SourceType]
    source: NotRequired[ExtractedSource]
    distillation: NotRequired[Distillation]
    error: NotRequired[str]  # why this one link failed; never fails the post


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
