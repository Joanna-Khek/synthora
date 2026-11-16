from telegram import Message, MessageEntity
from synthora.schemas import Capture, ParsedMessage

URL_ENTITY_TYPES = [MessageEntity.URL, MessageEntity.TEXT_LINK]


def parse_message(message: Message) -> ParsedMessage:
    if message.text is not None:
        text = message.text
        entities = message.parse_entities(URL_ENTITY_TYPES)
    else:
        text = message.caption or ""
        entities = message.parse_caption_entities(URL_ENTITY_TYPES)

    urls: list[str] = []
    comment = text
    for entity, entity_text in entities.items():
        url = entity.url if entity.type == MessageEntity.TEXT_LINK else entity_text
        if url not in urls:
            urls.append(url)
        if entity.type == MessageEntity.URL:
            comment = comment.replace(entity_text, " ")

    images = (message.photo[-1].file_id,) if message.photo else ()
    return ParsedMessage(
        message_id=message.message_id,
        urls=tuple(urls),
        image_file_ids=images,
        comment=" ".join(comment.split()) or None,
        media_group_id=message.media_group_id,
    )


def to_captures(parts: list[ParsedMessage]) -> list[Capture]:
    comment = "\n".join(p.comment for p in parts if p.comment) or None
    captures: list[Capture] = []
    for p in parts:
        captures += [Capture(kind="url", value=u, comment=comment) for u in p.urls]
        captures += [
            Capture(kind="image", value=f, comment=comment) for f in p.image_file_ids
        ]
    return captures
