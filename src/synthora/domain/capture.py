from typing import Literal

from pydantic import BaseModel, ConfigDict


class Capture(BaseModel):
    """One thing to learn from a URL or an image"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["url", "image"]
    value: str  # the URL or the telegram file_id of the image
    comment: str | None = None  # any comments alongside the capture object
