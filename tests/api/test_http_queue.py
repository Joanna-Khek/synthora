"""HttpQueue against the real pull API: the laptop and server sides of 8.4 together.

FastAPI's TestClient is an httpx.Client, so HttpQueue uses it as is: no network.
"""

import pytest
from fastapi.testclient import TestClient

from synthora import store
from synthora.api.app import create_app
from synthora.domain import Capture, Distillation, SourceType
from synthora.queue import HttpQueue, QueueError

TOKEN = "test-token"
IDS = {"chat_id": 1, "user_message_id": 10, "reply_message_id": 11}


def add_queued(db_path, url="https://example.com/a") -> int:
    conn = store.connect(db_path)
    capture_id = store.add_capture(conn, Capture(kind="url", value=url), **IDS)
    store.mark_queued(
        conn,
        capture_id,
        source_type="web_article",
        title="A title",
        distillation=Distillation(tldr="gist", key_ideas=["idea"], notable_quotes=[]),
        full_text="text",
        linked=[],
    )
    conn.close()
    return capture_id


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "synthora.db"


@pytest.fixture
def queue(db_path):
    app = create_app(db_path, TOKEN)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield HttpQueue(client)


def test_pending_comes_back_typed(queue, db_path):
    capture_id = add_queued(db_path)
    (item,) = queue.pending()
    assert item.id == capture_id
    assert item.source_type is SourceType.WEB_ARTICLE
    assert item.distillation.tldr == "gist"


def test_claim_and_ack_take_it_off_the_queue(queue, db_path):
    capture_id = add_queued(db_path)
    queue.claim(capture_id)
    queue.ack(capture_id)
    queue.ack(capture_id)  # idempotent, like LocalQueue
    assert queue.pending() == []


def test_acking_an_unclaimed_item_raises_queue_error(queue, db_path):
    capture_id = add_queued(db_path)
    with pytest.raises(QueueError):
        queue.ack(capture_id)


def test_a_wrong_token_is_an_http_error(db_path):
    with TestClient(
        create_app(db_path, TOKEN), headers={"Authorization": "Bearer no"}
    ) as c:
        with pytest.raises(Exception, match="401"):
            HttpQueue(c).pending()
