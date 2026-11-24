import asyncio

from langchain_anthropic import ChatAnthropic
from loguru import logger
from telegram import LinkPreviewOptions, Message, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from synthora import store
from synthora.bot import parsing, progress, replies, status
from synthora.capture.distiller import make_distill_model
from synthora.capture.link_picker import make_picker_model
from synthora.capture.pipeline import CapturePipeline, MessageIds, ProgressEvent
from synthora.capture.reader import ExtractionClient
from synthora.capture.tools import LiveTools
from synthora.config import (
    ROOT,
    BotConfig,
    CaptureConfig,
    LLMConfig,
    Settings,
    StorageConfig,
)
from synthora.domain import Capture
from synthora.tracing import setup_tracing


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! Send me a link and I'll show you what I can read from it."
    )


async def on_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    replied_to = message.reply_to_message
    if (
        replied_to
        and replied_to.from_user
        and replied_to.from_user.id == context.bot.id
    ):
        failed = store.find_failed_by_reply(
            context.bot_data["db"], message.chat_id, replied_to.message_id
        )
        if failed:
            context.application.create_task(
                complete_from_reply(message, failed, context.bot_data)
            )
            return
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
    pipeline: CapturePipeline = bot_data["pipeline"]
    ids = MessageIds(
        chat_id=ack.chat_id,
        user_message_id=ack.reply_to_message.message_id,
        reply_message_id=ack.message_id,
    )

    async def run_one(i: int, capture: Capture) -> None:
        log = progress.ProgressLog()

        async def on_progress(event: ProgressEvent) -> None:
            log.add(event)
            sections[i] = f"{capture.value[:60]}\n{log.text()}"
            await live.show(SEPARATOR.join(sections))

        try:
            outcome = await pipeline.run(capture, ids, on_progress)
            sections[i] = replies.render(outcome)
        except Exception as e:  # a bug: report it, don't let it hide the other captures
            logger.opt(exception=e).error(f"bug while processing {capture.value}")
            sections[i] = (
                f"💥 bug while processing {capture.value[:80]}: {type(e).__name__}"
            )

    await asyncio.gather(*(run_one(i, c) for i, c in enumerate(captures)))
    await live.show(SEPARATOR.join(sections), final=True)


async def complete_from_reply(message: Message, failed: list, bot_data: dict) -> None:
    """FR-22: use the user's pasted text to complete a Failed Capture."""
    if len(failed) > 1:
        await message.reply_text(
            f"That message has {len(failed)} failed Captures, so I can't tell which "
            "one this completes. Send each link on its own, then reply to its failure.",
            do_quote=True,
        )
        return
    if not message.text:
        await message.reply_text(
            "Screenshots aren't supported yet. Paste the text instead.", do_quote=True
        )
        return

    ack = await message.reply_text("Distilling your text…", do_quote=True)
    outcome = await bot_data["pipeline"].complete(failed[0], message.text)
    await ack.edit_text(replies.render_completed(outcome))


def make_owner_gate(owner_id: int):
    async def owner_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or user.id != owner_id:
            logger.warning(f"Ignored update from user.id={user.id if user else None}")
            raise ApplicationHandlerStop

    return owner_gate


async def start_extraction_server(app: Application) -> None:
    extraction = ExtractionClient()
    path = ROOT / app.bot_data["storage"].db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    await extraction.start()  # launches the Extraction Server as a child process
    app.bot_data["extraction"] = extraction

    api_key: str = app.bot_data["llm_api_key"]
    llm: LLMConfig = app.bot_data["llm"]
    picker_chat = ChatAnthropic(model=llm.model, api_key=api_key, max_tokens=1024)  # pyright: ignore[reportCallIssue]
    tools = LiveTools(
        extraction=extraction,
        distill_model=make_distill_model(api_key, llm),
        picker_model=make_picker_model(picker_chat),
        limits=app.bot_data["capture"],
    )
    app.bot_data["db"] = store.connect(path)
    app.bot_data["pipeline"] = CapturePipeline(app.bot_data["db"], tools)


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        status.build_status(context.bot_data["db"]),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def stop_extraction_server(app: Application) -> None:
    await app.bot_data["extraction"].stop()
    app.bot_data["db"].close()


def build_app(
    token: str,
    owner_id: int,
    bot: BotConfig,
    llm_api_key: str,
    llm: LLMConfig,
    storage: StorageConfig,
    capture: CaptureConfig,
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
    app.bot_data["storage"] = storage
    app.bot_data["capture"] = capture
    app.add_handler(
        TypeHandler(Update, make_owner_gate(owner_id)), group=-1
    )  # runs first
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_capture)
    )
    app.add_handler(CommandHandler("status", show_status))

    return app


# Bot don't handle the youtube key or github token. Only the extraction server processs reads them.
def main() -> None:
    settings = Settings()
    setup_tracing(config=settings.tracing)

    app = build_app(
        token=settings.telegram_token.get_secret_value(),
        owner_id=settings.owner_telegram_id,
        bot=settings.bot,
        llm_api_key=settings.llm_api_key.get_secret_value(),
        llm=settings.llm,
        storage=settings.storage,
        capture=settings.capture,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
