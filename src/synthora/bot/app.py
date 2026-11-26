import asyncio
from datetime import tzinfo

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
from synthora.bot.deps import DEPS_KEY, BotDeps, close_deps, get_deps, open_deps
from synthora.capture.pipeline import MessageIds, ProgressEvent
from synthora.config import BotConfig, CaptureConfig, LLMConfig, Settings, StorageConfig
from synthora.domain import Capture
from synthora.tracing import setup_tracing


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! Send me a link and I'll show you what I can read from it."
    )


async def on_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    deps = get_deps(context)
    replied_to = message.reply_to_message
    if (
        replied_to
        and replied_to.from_user
        and replied_to.from_user.id == context.bot.id
    ):
        failed = store.find_failed_by_reply(
            deps.db, message.chat_id, replied_to.message_id
        )
        if failed:
            context.application.create_task(complete_from_reply(message, failed, deps))
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
        # Run in the background so the next message isn't stuck behind this one.
        context.application.create_task(process(ack, captures, deps))
        return

    # If its an image
    parts = deps.albums.setdefault(parsed.media_group_id, [])
    parts.append(parsed)
    if len(parts) == 1:  # first photo: start timer
        ack = await message.reply_text("Processing...", do_quote=True)
        context.application.create_task(flush_album(ack, parsed.media_group_id, deps))


async def flush_album(ack: Message, group_id: str, deps: BotDeps) -> None:
    await asyncio.sleep(deps.config.album_wait_seconds)
    parts = deps.albums.pop(group_id)
    logger.info(f"album {group_id}: {len(parts)} parts")
    await process(ack, parsing.to_captures(parts), deps)


SEPARATOR = "\n\n———\n\n"


async def process(ack: Message, captures: list[Capture], deps: BotDeps) -> None:
    """Run every Capture at the same time, showing live progress in one message."""
    live = progress.LiveMessage(ack, max_chars=deps.config.max_reply_chars)
    sections = ["⏳ waiting…"] * len(captures)
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
            outcome = await deps.pipeline.run(capture, ids, on_progress)
            sections[i] = replies.render(outcome)
        except Exception as e:  # a bug: report it, don't let it hide the other captures
            logger.opt(exception=e).error(f"bug while processing {capture.value}")
            sections[i] = (
                f"💥 bug while processing {capture.value[:80]}: {type(e).__name__}"
            )

    await asyncio.gather(*(run_one(i, c) for i, c in enumerate(captures)))
    await live.show(SEPARATOR.join(sections), final=True)


async def complete_from_reply(message: Message, failed: list, deps: BotDeps) -> None:
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
    outcome = await deps.pipeline.complete(failed[0], message.text)
    await ack.edit_text(replies.render_completed(outcome))


def make_owner_gate(owner_id: int):
    async def owner_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or user.id != owner_id:
            logger.warning(f"Ignored update from user.id={user.id if user else None}")
            raise ApplicationHandlerStop

    return owner_gate


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        status.build_status(get_deps(context).db, get_deps(context).timezone),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def build_app(
    token: str,
    owner_id: int,
    bot: BotConfig,
    llm_api_key: str,
    llm: LLMConfig,
    storage: StorageConfig,
    capture: CaptureConfig,
    timezone: tzinfo,
) -> Application:
    async def post_init(app: Application) -> None:  # runs once, after the bot starts
        app.bot_data[DEPS_KEY] = await open_deps(
            bot, llm_api_key, llm, storage, capture, timezone=timezone
        )

    async def post_shutdown(app: Application) -> None:  # runs once, on Ctrl+C
        deps = app.bot_data.get(DEPS_KEY)
        if deps is not None:  # None when post_init failed before storing them
            await close_deps(deps)

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
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
        timezone=settings.timezone,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
