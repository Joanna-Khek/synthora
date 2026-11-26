"""`synthora-runner`: check the queue cheaply, and start the full runner only when there is work.

The full runner (runner/main.py) imports LangChain, MLflow and the Obsidian code and
starts the Content Server: ~10 s each time. Task Scheduler runs this every minute, and
most minutes the queue is empty, so the check uses only the queue and config (~0.4 s).
"""

import asyncio
import sys
from contextlib import ExitStack

import httpx

from synthora import store
from synthora.config import ROOT, Settings
from synthora.queue import HttpQueue, LocalQueue, Queue


def open_queue(settings: Settings, stack: ExitStack) -> Queue:
    """The pull API when `pull_api:` is configured (the bot runs on Fly), else the local DB.

    Whatever is opened is closed when `stack` closes.
    """
    if settings.pull_api is None:
        conn = store.connect(ROOT / settings.storage.db_path)
        stack.callback(
            conn.close
        )  # not enter_context: `with conn` only commits, it doesn't close
        return LocalQueue(conn, settings.timezone)
    if settings.pull_api_token is None:
        sys.exit("pull_api.url is set but PULL_API_TOKEN isn't: add it to .env")
    client = httpx.Client(
        base_url=settings.pull_api.url,
        headers={
            "Authorization": f"Bearer {settings.pull_api_token.get_secret_value()}"
        },
        timeout=30,
    )
    return HttpQueue(stack.enter_context(client))


def has_work(settings: Settings) -> bool:
    with ExitStack() as stack:
        return bool(open_queue(settings, stack).pending(limit=1))


def main() -> None:
    settings = Settings()  # pyright: ignore[reportCallIssue]  (filled from .env and config.yaml)
    if not has_work(settings):
        return  # silent: an empty minute shouldn't add a line to the log
    from synthora.runner.main import run, summary  # the heavy part, only when needed

    print(summary(asyncio.run(run())))


if __name__ == "__main__":
    main()
