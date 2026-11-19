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
from langchain_anthropic import ChatAnthropic

from synthora.server.bot import parsing, source_type, progress
from synthora.server.schemas import Capture
from synthora.agent.graph import build_capture_graph
from synthora.agent.link_picker import make_picker_model
from synthora.agent.tools import Tools
from synthora.config import BotConfig, LLMConfig, Settings
from synthora.server.distiller import make_distill_model
from synthora.server.reader import ExtractionConnection


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
    await asyncio.sleep(bot_data["config"].album_wait_seconds)
    parts = albums.pop(group_id)
    logger.info(f"album {group_id}: {len(parts)} parts")
    await process(ack, parsing.to_captures(parts), bot_data)


SEPARATOR = "\n\n———\n\n"


async def process(ack: Message, captures: list[Capture], bot_data: dict) -> None:
    """Run every Capture at the same time, showing live progress in one message."""
    live = progress.LiveMessage(ack)
    sections = ["⏳ waiting…"] * len(captures)

    async def run_one(i: int, capture: Capture) -> None:
        async def on_progress(text: str) -> None:
            sections[i] = f"{capture.value[:60]}\n{text}"
            await live.show(SEPARATOR.join(sections))

        try:
            sections[i] = await run_capture(capture, bot_data, on_progress)
        except Exception as e:  # a bug: report it, don't let it hide the other captures
            logger.opt(exception=e).error(f"bug while processing {capture.value}")
            sections[i] = (
                f"💥 bug while processing {capture.value[:80]}: {type(e).__name__}"
            )

    await asyncio.gather(*(run_one(i, c) for i, c in enumerate(captures)))
    await live.show(SEPARATOR.join(sections), final=True)


async def run_capture(capture: Capture, bot_data: dict, on_progress) -> str:
    """Run one Capture through the graph, reporting each step; return the reply text."""
    lines: list[str] = []
    counter = ""
    reply_text = ""
    async for mode, chunk in bot_data["graph"].astream(
        {"capture": capture},
        context=bot_data["tools"],
        stream_mode=["updates", "custom"],
    ):
        if mode == "updates":
            for node, update in chunk.items():
                update = update or {}
                reply_text = update.get("reply_text", reply_text)
                line = progress.describe_update(node, update)
                if line:
                    lines.append(line)
                    counter = ""  # a step finished, so its counter is no longer needed
        else:  # "custom": a counter from read_linked or distill
            counter = f"⏳ {chunk['step']}: {chunk['done']}/{chunk['total']}"
        await on_progress("\n".join(lines + [counter or "⏳ working…"]))
    return reply_text


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


async def start_extraction_server(app: Application) -> None:
    connection = ExtractionConnection()
    await connection.start()  # launches the Extraction Server as a child process
    app.bot_data["extraction"] = connection

    # The client only exists after start(), so the Tools are built here.
    api_key: str = app.bot_data["llm_api_key"]
    llm: LLMConfig = app.bot_data["llm"]
    picker_chat = ChatAnthropic(model=llm.model, api_key=api_key, max_tokens=1024)  # pyright: ignore[reportCallIssue]
    app.bot_data["tools"] = Tools(
        client=connection.client,
        distill_model=make_distill_model(api_key, llm),
        picker_model=make_picker_model(picker_chat),
    )
    app.bot_data["graph"] = build_capture_graph()


async def stop_extraction_server(app: Application) -> None:
    await app.bot_data["extraction"].stop()


def build_app(
    token: str,
    owner_id: int,
    bot: BotConfig,
    llm_api_key: str,
    llm: LLMConfig,
) -> Application:
    app = (
        Application.builder()
        .token(token)
        .post_init(start_extraction_server)  # runs once, after the bot starts
        .post_shutdown(stop_extraction_server)  # runs once, when you press Ctrl+C
        .build()
    )
    app.bot_data["config"] = bot
    app.bot_data["llm_api_key"] = llm_api_key
    app.bot_data["llm"] = llm
    app.add_handler(
        TypeHandler(Update, make_owner_gate(owner_id)), group=-1
    )  # runs first
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_capture)
    )
    return app


# Bot don't handle the youtube key or github token. Only the extraction server processs reads them.
def main() -> None:
    settings = Settings()
    app = build_app(
        token=settings.telegram_token.get_secret_value(),
        owner_id=settings.owner_telegram_id,
        bot=settings.bot,
        llm_api_key=settings.llm_api_key.get_secret_value(),
        llm=settings.llm,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
