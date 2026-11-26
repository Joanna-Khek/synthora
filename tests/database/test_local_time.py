"""Times are stored in UTC and shown in the configured zone (here UTC+8)."""

from datetime import date
from zoneinfo import ZoneInfo

from synthora import store
from synthora.bot.status import build_status
from synthora.domain import Capture, Distillation
from synthora.queue import LocalQueue

SINGAPORE = ZoneInfo("Asia/Singapore")
IDS = {"chat_id": 1, "user_message_id": 10, "reply_message_id": 11}


def test_local_time_converts_from_utc():
    t = store.local_time("2026-11-25T05:32:00.000Z", SINGAPORE)
    assert (t.hour, t.minute) == (13, 32)


def add_queued_at(conn, created_at: str) -> int:
    capture_id = store.add_capture(
        conn, Capture(kind="url", value="https://a.com"), **IDS
    )
    store.mark_queued(
        conn,
        capture_id,
        source_type="web_article",
        title="A",
        distillation=Distillation(tldr="x", key_ideas=[], notable_quotes=[]),
        full_text="text",
        linked=[],
    )
    conn.execute(
        "UPDATE captures SET created_at = ? WHERE id = ?", (created_at, capture_id)
    )
    return capture_id


def test_status_shows_local_time():
    conn = store.connect(":memory:")
    add_queued_at(conn, "2026-11-25T05:32:00.000Z")
    assert "2026-11-25 13:32" in build_status(conn, SINGAPORE)


def test_captured_is_the_local_date_after_midnight():
    # 23:30 UTC on the 25th is 07:30 on the 26th in Singapore
    conn = store.connect(":memory:")
    add_queued_at(conn, "2026-11-25T23:30:00.000Z")
    (item,) = LocalQueue(conn, SINGAPORE).pending()
    assert item.captured == date(2026, 11, 26)
