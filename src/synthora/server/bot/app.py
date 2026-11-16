import asyncio
from loguru import logger
from telegram import Update, LinkPreviewOptions, Message
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from synthora.config import Settings
from synthora.server.bot import parsing, source_type
from synthora.schemas import Capture

ALBUM_WAIT_SECONDS = 1.5
FAKE_PROCESSING_SECONDS = 3


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hi! Send me anything and I'll echo it.")


async def on_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    parsed = parsing.parse_message(message)
    logger.info(f"parsed: {parsed}")

    # If it is not an image, just a normal single message
    if parsed.media_group_id is None:
        captures = parsing.to_captures([parsed])
        if not captures:
            await message.reply_text(
                "No link of image found, so nothing to capture.", do_quote=True
            )
            return
        ack = await message.reply_text("Processing...", do_quote=True)
        await process(ack, captures)
        return

    # If its an image
    albums: dict[str, list] = context.bot_data.setdefault("albums", {})
    parts = albums.setdefault(parsed.media_group_id, [])
    parts.append(parsed)
    if len(parts) == 1:  # first photo: start timer
        ack = await message.reply_text("Processing...", do_quote=True)
        context.application.create_task(flush_album(ack, parsed.media_group_id, albums))


async def flush_album(ack: Message, group_id: str, albums: dict) -> None:
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    parts = albums.pop(group_id)
    logger.info(f"album {group_id}: {len(parts)} parts")
    await process(ack, parsing.to_captures(parts))


async def process(ack: Message, captures: list[Capture]) -> None:
    await asyncio.sleep(FAKE_PROCESSING_SECONDS)
    await ack.edit_text(
        format_captures(captures),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def format_captures(captures: list[Capture]) -> str:
    lines = [f"{i}. {c.kind}: {c.value[:80]}" for i, c in enumerate(captures, 1)]
    lines.append(f"\ncomment: {captures[0].comment!r}")
    lines = [
        f"{i}. {source_type.hint_for(c)}: {c.value[:80]}"
        for i, c in enumerate(captures, 1)
    ]
    return "\n".join(lines)


async def reply_with_captures(message: Message, captures: list[Capture]) -> None:
    if not captures:
        await message.reply_text("No link or image found, so nothing to capture")
        return
    lines = [f"{i}. {c.kind}: {c.value[:80]}" for i, c in enumerate(captures, 1)]
    lines.append(f"\ncomment: {captures[0].comment!r}")
    await message.reply_text(
        "\n".join(lines),
        link_preview_options=LinkPreviewOptions(
            is_disabled=True
        ),  # keep replies compact
    )


def make_owner_gate(owner_id: int):
    async def owner_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or user.id != owner_id:
            logger.warning(f"Ignored update from user.id={user.id if user else None}")
            raise ApplicationHandlerStop

    return owner_gate


def build_app(token: str, owner_id: int) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(
        TypeHandler(Update, make_owner_gate(owner_id)), group=-1
    )  # runs first
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_capture)
    )
    return app


def main() -> None:
    settings = Settings()
    app = build_app(
        token=settings.telegram_token.get_secret_value(),
        owner_id=settings.owner_telegram_id,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
