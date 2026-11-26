"""The pull API, on a real SQLite file, through FastAPI's TestClient."""

import pytest
from fastapi.testclient import TestClient

from synthora import store
from synthora.api.app import create_app
from synthora.domain import Capture, Distillation

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
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
def client(db_path):
    with TestClient(create_app(db_path, TOKEN)) as c:  # `with` runs the lifespan
        yield c


def test_health_needs_no_token(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_queue_refuses_a_missing_or_wrong_token(client, headers):
    assert client.get("/queue", headers=headers).status_code == 401
    assert client.post("/ack/1", headers=headers).status_code == 401


def test_a_queued_capture_comes_back_as_json(client, db_path):
    capture_id = add_queued(db_path)
    (item,) = client.get("/queue", headers=AUTH).json()
    assert item["id"] == capture_id
    assert item["url"] == "https://example.com/a"
    assert item["distillation"]["tldr"] == "gist"


def test_claim_then_ack_takes_it_off_the_queue(client, db_path):
    capture_id = add_queued(db_path)
    assert client.post(f"/claim/{capture_id}", headers=AUTH).status_code == 204
    assert client.post(f"/ack/{capture_id}", headers=AUTH).status_code == 204
    assert client.get("/queue", headers=AUTH).json() == []


def test_claim_and_ack_are_idempotent(client, db_path):
    capture_id = add_queued(db_path)
    for _ in range(2):
        assert client.post(f"/claim/{capture_id}", headers=AUTH).status_code == 204
    for _ in range(2):
        assert client.post(f"/ack/{capture_id}", headers=AUTH).status_code == 204


def test_acking_an_unclaimed_item_is_a_conflict(client, db_path):
    capture_id = add_queued(db_path)
    assert client.post(f"/ack/{capture_id}", headers=AUTH).status_code == 409
