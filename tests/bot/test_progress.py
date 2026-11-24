"""Tests for LiveMessage: the "Processing..." message the bot keeps editing."""

import asyncio

from synthora.bot.progress import LiveMessage


class FakeMessage:
    """Records every edit instead of sending it to Telegram."""

    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text: str, **kwargs) -> None:
        self.edits.append(text)


def show(live: LiveMessage, *texts: str) -> None:
    async def go():
        for text in texts:
            await live.show(text, final=True)  # final: skip the throttle

    asyncio.run(go())


def test_text_is_trimmed_to_max_reply_chars():
    message = FakeMessage()
    show(LiveMessage(message, max_chars=10), "x" * 50)  # pyright: ignore[reportArgumentType]
    assert message.edits == ["x" * 10]


def test_an_unchanged_text_is_not_sent_again():
    message = FakeMessage()
    show(LiveMessage(message, max_chars=100), "same", "same")  # pyright: ignore[reportArgumentType]
    assert message.edits == ["same"]
