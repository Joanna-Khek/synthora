from datetime import datetime, timezone

from telegram import Chat, Message, MessageEntity, PhotoSize, User

from synthora.bot.parsing import parse_message, to_captures

GITHUB = "https://github.com/obsidianmd/obsidian-skills"
DLAI = "https://www.deeplearning.ai/courses/tabular-foundation-models-for-data-science-agents?utm_medium=social&utm_source=dlai-homepage"
LINKEDIN = "https://www.linkedin.com/posts/stasbel_best-12-github-repos-to-secure-your-ai-generated-share-7527015621663035392-npoh/?utm_source=share&utm_medium=member_ios&rcm=ACoAABX1_4oB8ZjceS24nXUbVDwSW-dL-pTzdro"

USER = User(id=111, first_name="Test", is_bot=False)
CHAT = Chat(id=111, type=Chat.PRIVATE)


def utf16_len(s: str) -> int:
    """Length the way Telegram counts it (UTF-16 code units)."""
    return len(s.encode("utf-16-le")) // 2


def url_entities(text: str, urls: list[str]) -> tuple[MessageEntity, ...]:
    """Build the URL entities Telegram would send, with UTF-16 offsets."""
    return tuple(
        MessageEntity(
            MessageEntity.URL,
            offset=utf16_len(text[: text.index(u)]),
            length=utf16_len(u),
        )
        for u in urls
    )


def text_message(text: str, urls: list[str]) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=CHAT,
        from_user=USER,
        text=text,
        entities=url_entities(text, urls),
    )


def photo_message(
    message_id: int, file_id: str, caption: str | None = None, group: str | None = None
) -> Message:
    sizes = (
        PhotoSize(
            f"{file_id}-small", f"{file_id}-su", 90, 90
        ),  # Telegram sends several sizes
        PhotoSize(file_id, f"{file_id}-u", 1280, 1280),
    )  # largest last
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=CHAT,
        from_user=USER,
        photo=sizes,
        caption=caption,
        media_group_id=group,
    )


def test_text_with_several_urls_gives_one_capture_each():
    text = f"Read these for the agents project\n{GITHUB}\n{DLAI}\n{LINKEDIN}"
    captures = to_captures(
        [parse_message(text_message(text, [GITHUB, DLAI, LINKEDIN]))]
    )
    assert [c.value for c in captures] == [GITHUB, DLAI, LINKEDIN]
    assert all(c.comment == "Read these for the agents project" for c in captures)


def test_emoji_before_url_does_not_shift_it():
    """Emoji are 2 UTF-16 units; a naive Python slice would cut the URL wrong."""
    parsed = parse_message(text_message(f"🔥🔥 must read {LINKEDIN}", [LINKEDIN]))
    assert parsed.urls == (LINKEDIN,)
    assert parsed.comment == "🔥🔥 must read"


def test_url_only_has_no_comment():
    assert parse_message(text_message(GITHUB, [GITHUB])).comment is None


def test_image_with_caption():
    parsed = parse_message(photo_message(1, "img1", caption="RAG cheat sheet"))
    assert parsed.image_file_ids == ("img1",)  # the largest size, not the thumbnail
    assert parsed.comment == "RAG cheat sheet"


def test_album_of_three_shares_the_first_caption():
    parts = [
        photo_message(1, "a", caption="infographics", group="g1"),
        photo_message(2, "b", group="g1"),
        photo_message(3, "c", group="g1"),
    ]
    captures = to_captures([parse_message(m) for m in parts])
    assert [c.value for c in captures] == ["a", "b", "c"]
    assert all(c.comment == "infographics" for c in captures)
