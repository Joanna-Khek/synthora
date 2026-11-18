import re
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
from synthora.server.extraction import ExtractionError, make_client
from synthora.server.reader import read_url


ALBUM_WAIT_SECONDS = 1.5
PREVIEW_CHARS = 10000
TELEGRAM_MAX_CHARS = 4096
# Markdown links: [label](url) and images ![alt](url). The url may contain one level of (...).
MARKDOWN_LINK = re.compile(r"(!?)\[([^\]]*)\]\(((?:[^()\s]|\([^()]*\))*)\)")


def strip_markdown_links(text: str) -> str:
    """'[label](url)' -> 'label'; images are dropped. For chat previews only."""

    def replace(match: re.Match) -> str:
        is_image, label, url = match.groups()
        if is_image:
            return ""  # images can't be shown in a text reply
        return label or url  # keep the visible text; fall back to the url if empty

    return MARKDOWN_LINK.sub(replace, text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! Send me a link and I'll show you what I can read from it."
    )


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
        # await process(ack, captures)
        # Run in the background so the next message isn't stuck behind this one.
        context.application.create_task(process(ack, captures, context.bot_data))
        return

    # If its an image
    albums: dict[str, list] = context.bot_data.setdefault("albums", {})
    parts = albums.setdefault(parsed.media_group_id, [])
    parts.append(parsed)
    if len(parts) == 1:  # first photo: start timer
        ack = await message.reply_text("Processing...", do_quote=True)
        context.application.create_task(
            flush_album(ack, parsed.media_group_id, albums, context.bot_data)
        )


async def flush_album(
    ack: Message, group_id: str, albums: dict, bot_data: dict
) -> None:
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    parts = albums.pop(group_id)
    logger.info(f"album {group_id}: {len(parts)} parts")
    await process(ack, parsing.to_captures(parts), bot_data)


# async def process(ack: Message, captures: list[Capture]) -> None:
#     await asyncio.sleep(FAKE_PROCESSING_SECONDS)
#     await ack.edit_text(
#         format_captures(captures),
#         link_preview_options=LinkPreviewOptions(is_disabled=True),
#     )


async def process(ack: Message, captures: list[Capture], bot_data: dict) -> None:
    # Read every Capture at the same time; one crash doesn't stop the others.
    results = await asyncio.gather(
        *(read_one(c, bot_data) for c in captures), return_exceptions=True
    )
    sections = []
    for capture, result in zip(captures, results):
        if isinstance(result, Exception):
            logger.opt(exception=result).error(f"bug while reading {capture.value}")
            sections.append(
                f"💥 bug while reading {capture.value[:80]}: {type(result).__name__}"
            )
        else:
            sections.append(result)
    await ack.edit_text(
        "\n\n———\n\n".join(sections)[:TELEGRAM_MAX_CHARS],
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def read_one(capture: Capture, bot_data: dict) -> str:
    """Read one Capture and describe the outcome as a short chat message."""
    kind = source_type.hint_for(capture)
    if capture.kind == "image":
        return f"{kind}\nDetected image."

    try:
        result = await read_url(
            bot_data["http"],
            capture.value,
            kind,
            youtube_key=bot_data["youtube_key"],
            github_token=bot_data["github_token"],
        )
    except ExtractionError as e:
        logger.info(f"failed {capture.value}: {e}")
        return f"📂 {kind}\n❌ {str(e)[:300]}"

    title = " ".join((result.title or "(no title)").split())[:120]
    preview = " ".join(strip_markdown_links(result.text).split())[:PREVIEW_CHARS]

    return (
        f"{kind} → {result.tool}\n\n"
        f"✅ {title}\n"
        f"by {result.author or '?'} · {kind} via {result.tool} · {len(result.text)} chars\n"
        f"{result.url}\n\n"
        f"{preview}…"
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
        await message.reply_text("No link of image found, so nothing to capture")
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


async def open_http_client(app: Application) -> None:
    app.bot_data["http"] = make_client()  # one shared client for the whole bot


async def close_http_client(app: Application) -> None:
    await app.bot_data["http"].aclose()


def build_app(
    token: str,
    owner_id: int,
    youtube_key: str = "",
    github_token: str | None = None,
) -> Application:
    app = (
        Application.builder()
        .token(token)
        .post_init(open_http_client)  # runs once, after the bot starts
        .post_shutdown(close_http_client)  # runs once, when you press Ctrl+C
        .build()
    )
    app.bot_data["youtube_key"] = youtube_key
    app.bot_data["github_token"] = github_token
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
        youtube_key=settings.google_api_key.get_secret_value(),
        github_token=(
            settings.github_token.get_secret_value() if settings.github_token else None
        ),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
