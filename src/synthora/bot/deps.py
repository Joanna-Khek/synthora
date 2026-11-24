"""What the bot's handlers need, built once at startup and closed once at shutdown."""

import sqlite3

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, ConfigDict
from telegram.ext import ContextTypes

from synthora import store
from synthora.bot.parsed_message import ParsedMessage
from synthora.capture.distiller import make_distill_model
from synthora.capture.link_picker import make_picker_model
from synthora.capture.pipeline import CapturePipeline
from synthora.capture.reader import ExtractionClient
from synthora.capture.tools import LiveTools
from synthora.config import ROOT, BotConfig, CaptureConfig, LLMConfig, StorageConfig

DEPS_KEY = "deps"  # the only key the bot keeps in PTB's bot_data


class BotDeps(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    config: BotConfig
    db: sqlite3.Connection
    extraction: ExtractionClient
    pipeline: CapturePipeline
    # Photo albums still arriving, by media_group_id. The dict itself is
    # mutable state; frozen only stops it being replaced.
    albums: dict[str, list[ParsedMessage]] = {}


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> BotDeps:
    return context.bot_data[DEPS_KEY]


async def open_deps(
    bot: BotConfig,
    llm_api_key: str,
    llm: LLMConfig,
    storage: StorageConfig,
    capture: CaptureConfig,
) -> BotDeps:
    """Start the Extraction Server, open the database and build the pipeline."""
    path = ROOT / storage.db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    extraction = ExtractionClient()
    await extraction.start()  # launches the Extraction Server as a child process

    picker_chat = ChatAnthropic(model=llm.model, api_key=llm_api_key, max_tokens=1024)  # pyright: ignore[reportCallIssue]
    tools = LiveTools(
        extraction=extraction,
        distill_model=make_distill_model(llm_api_key, llm),
        picker_model=make_picker_model(picker_chat),
        limits=capture,
    )
    db = store.connect(path)
    return BotDeps(
        config=bot,
        db=db,
        extraction=extraction,
        pipeline=CapturePipeline(db, tools),
    )


async def close_deps(deps: BotDeps) -> None:
    await deps.extraction.stop()
    deps.db.close()
