"""8.4: the pull API. The laptop's HttpQueue calls it to take queued Sources and ack them.

It runs as its own process next to the bot, on the same SQLite file (WAL mode lets
one process write while another reads). It imports no Telegram, LangGraph or MLflow,
so it stays small.
"""

import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from datetime import UTC, tzinfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from synthora import store
from synthora.queue import LocalQueue, QueueError, QueuedSource

bearer = HTTPBearer(auto_error=False)  # we raise the 401 ourselves, with one message


def create_app(db_path: Path, token: str, tz: tzinfo = UTC) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # One connection, opened on the event loop's thread. Every endpoint is
        # `async def`, so they all run on that same thread: sqlite3 requires it.
        conn = store.connect(db_path)
        app.state.queue = LocalQueue(conn, tz)
        yield
        conn.close()

    def require_token(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        # compare_digest takes the same time whatever the input, so an attacker
        # can't guess the token one character at a time from response times.
        if creds is None or not secrets.compare_digest(
            creds.credentials.encode(), token.encode()
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "missing or wrong bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def get_queue(request: Request) -> LocalQueue:
        return request.app.state.queue

    Queue = Annotated[LocalQueue, Depends(get_queue)]

    app = FastAPI(
        title="Synthora pull API",
        lifespan=lifespan,
        docs_url=None,  # no public /docs, /redoc or schema: this API has one client
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")  # no token: for uptime checks (8.6)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/queue", dependencies=[Depends(require_token)])
    async def pending(
        queue: Queue, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> list[QueuedSource]:
        return queue.pending(limit)

    @app.post(
        "/claim/{item_id}",
        dependencies=[Depends(require_token)],
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def claim(item_id: int, queue: Queue) -> None:
        queue.claim(item_id)  # claiming twice is harmless

    @app.post(
        "/ack/{item_id}",
        dependencies=[Depends(require_token)],
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def ack(item_id: int, queue: Queue) -> None:
        try:
            queue.ack(item_id)  # acking twice is harmless
        except QueueError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    return app


def main() -> None:
    import uvicorn
    from loguru import logger

    from synthora.config import ROOT, Settings

    settings = Settings()
    if settings.pull_api_token is None:
        raise SystemExit(
            "PULL_API_TOKEN is not set; refusing to serve the queue without auth"
        )
    app = create_app(
        ROOT / settings.storage.db_path,
        settings.pull_api_token.get_secret_value(),
        tz=settings.timezone,
    )
    logger.info("pull API on :8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)  # Fly's proxy connects over IPv4
