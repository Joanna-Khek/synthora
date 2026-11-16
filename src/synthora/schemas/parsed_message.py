from pydantic import BaseModel, ConfigDict


class ParsedMessage(BaseModel):
    """The content of one Telegram message"""

    model_config = ConfigDict(frozen=True)

    message_id: int
    urls: tuple[str, ...]
    image_file_ids: tuple[str, ...]
    comment: str | None
    media_group_id: str | None
