"""Synthora's shared vocabulary (see CONTEXT.md). No I/O; imported by every process."""

from .capture import Capture
from .distillation import Distillation
from .errors import ExtractionError
from .extracted_source import ExtractedSource
from .source_type import SourceType, guess_source_type, hint_for

__all__ = [
    "Capture",
    "Distillation",
    "ExtractedSource",
    "ExtractionError",
    "SourceType",
    "guess_source_type",
    "hint_for",
]
