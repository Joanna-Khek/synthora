import sqlite3

import pytest

from synthora.server.database.store import MIGRATIONS, connect
from synthora.server.database.store import (
    add_capture,
    find_successful,
    mark_failed,
    mark_queued,
    find_failed_by_reply,
    complete_failed,
    count_by_status,
    pending_captures,
    recent_failures,
    mark_pulled,
    mark_written,
)
from synthora.server.bot.status import build_status
from synthora.server.schemas import Capture


def _insert_capture(conn: sqlite3.Connection, **overrides) -> int:
    row = {
        "kind": "url",
        "value": "https://example.com",
        "chat_id": 1,
        "user_message_id": 10,
    }
    row |= overrides
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    return conn.execute(
        f"INSERT INTO captures ({cols}) VALUES ({marks})", row
    ).lastrowid


def test_new_database_is_at_latest_version(tmp_path):
    conn = connect(tmp_path / "synthora.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_reconnecting_does_not_rerun_migrations(tmp_path):
    path = tmp_path / "synthora.db"
    connect(path).close()
    connect(path).close()  # re-running v1 would raise "table captures already exists"


def test_new_capture_gets_defaults(tmp_path):
    conn = connect(tmp_path / "synthora.db")
    row = conn.execute(
        "SELECT * FROM captures WHERE id = ?", (_insert_capture(conn),)
    ).fetchone()
    assert row["status"] == "processing"
    assert row["created_at"].endswith("Z")


def test_unknown_status_is_rejected(tmp_path):
    conn = connect(tmp_path / "synthora.db")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_capture(conn, status="done")


def test_failure_must_point_at_a_real_capture(tmp_path):
    conn = connect(tmp_path / "synthora.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO failures (capture_id, reason) VALUES (999, 'x')")


URL = "https://example.com/post"
IDS = {"chat_id": 1, "user_message_id": 10, "reply_message_id": 11}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "synthora.db")
    yield c
    c.close()


def _add(conn, url=URL) -> int:
    return add_capture(conn, Capture(kind="url", value=url), **IDS)


def test_successful_capture_is_found(conn):
    mark_queued(
        conn, _add(conn), source_type="web_article", tldr="the gist", full_text="..."
    )
    assert find_successful(conn, URL)["tldr"] == "the gist"


def test_failed_capture_does_not_count(conn):
    mark_failed(conn, _add(conn), reason="404", source_type=None)
    assert find_successful(conn, URL) is None


def test_capture_still_processing_does_not_count(conn):
    _add(conn)
    assert find_successful(conn, URL) is None


def test_match_is_exact(conn):
    mark_queued(conn, _add(conn), source_type="web_article", tldr="x", full_text="x")
    assert find_successful(conn, URL + "/") is None  # FR-23: no normalisation


def test_failure_reason_is_logged(conn):
    capture_id = _add(conn)
    mark_failed(conn, capture_id, reason="404", source_type=None)
    rows = conn.execute(
        "SELECT reason FROM failures WHERE capture_id = ?", (capture_id,)
    )
    assert [r["reason"] for r in rows] == ["404"]


def test_captures_from_one_message_share_a_reply(conn):
    _add(conn, "https://a.com")
    _add(
        conn, "https://b.com"
    )  # same reply_message_id; failed while the index was UNIQUE


def test_reply_finds_only_the_failed_captures(conn):
    ok = _add(conn, "https://a.com")
    mark_queued(conn, ok, source_type="web_article", tldr="x", full_text="x")
    bad = _add(conn, "https://b.com")
    mark_failed(conn, bad, reason="404", source_type=None)
    rows = find_failed_by_reply(conn, chat_id=1, reply_message_id=11)
    assert [r["id"] for r in rows] == [bad]


def test_reply_lookup_is_per_chat(conn):
    mark_failed(conn, _add(conn), reason="404", source_type=None)
    assert find_failed_by_reply(conn, chat_id=2, reply_message_id=11) == []


def test_completing_a_failed_capture(conn):
    capture_id = _add(conn)
    mark_failed(conn, capture_id, reason="404", source_type=None)

    assert complete_failed(conn, capture_id, tldr="the gist", full_text="pasted")

    assert find_successful(conn, URL)["tldr"] == "the gist"
    failure = conn.execute(
        "SELECT resolved_at FROM failures WHERE capture_id = ?", (capture_id,)
    ).fetchone()
    assert failure["resolved_at"] is not None


def test_only_failed_captures_can_be_completed(conn):
    capture_id = _add(conn)  # still 'processing'
    assert not complete_failed(conn, capture_id, tldr="x", full_text="x")
    status = conn.execute(
        "SELECT status FROM captures WHERE id = ?", (capture_id,)
    ).fetchone()
    assert status["status"] == "processing"


def test_pending_lists_only_queued_and_pulled(conn):
    mark_queued(
        conn,
        _add(conn, "https://a.com"),
        source_type="web_article",
        tldr="x",
        full_text="x",
    )
    _add(conn, "https://b.com")  # still processing
    mark_failed(conn, _add(conn, "https://c.com"), reason="404", source_type=None)
    assert [r["value"] for r in pending_captures(conn)] == ["https://a.com"]


def test_count_by_status(conn):
    _add(conn, "https://a.com")
    mark_failed(conn, _add(conn, "https://b.com"), reason="404", source_type=None)
    assert count_by_status(conn) == {"processing": 1, "failed": 1}


def test_failure_is_hidden_once_the_url_succeeds(conn):
    mark_failed(conn, _add(conn), reason="timeout", source_type=None)
    assert len(recent_failures(conn)) == 1

    mark_queued(conn, _add(conn), source_type="web_article", tldr="x", full_text="x")
    assert recent_failures(conn) == []


def test_status_on_an_empty_database(conn):
    text = build_status(conn)
    assert "none yet" in text
    assert "Waiting for the Vault (0)" in text
    assert "Recent failures (0)" in text


def test_status_shows_the_failure_reason(conn):
    mark_failed(conn, _add(conn), reason="404 not found", source_type=None)
    assert "404 not found" in build_status(conn)


def test_status_says_how_many_more(conn):
    for i in range(3):
        mark_queued(
            conn,
            _add(conn, f"https://{i}.com"),
            source_type="web_article",
            tldr="x",
            full_text="x",
        )
    assert "…and 1 more" in build_status(conn, limit=2)


STATUSES = ["processing", "queued", "pulled", "written", "failed"]

# action: (statuses it may start from, status it ends in) — this table IS the spec
ALLOWED = {
    "queue": ({"processing"}, "queued"),
    "fail": ({"processing"}, "failed"),
    "pull": ({"queued"}, "pulled"),
    "write": ({"pulled", "written"}, "written"),  # written → written: repeated ack
    "complete": ({"failed"}, "queued"),
}

ACTIONS = {
    "queue": lambda conn, cid: mark_queued(
        conn, cid, source_type="web_article", tldr="x", full_text="x"
    ),
    "fail": lambda conn, cid: mark_failed(conn, cid, reason="x", source_type=None),
    "pull": mark_pulled,
    "write": mark_written,
    "complete": lambda conn, cid: complete_failed(conn, cid, tldr="x", full_text="x"),
}


def _status(conn, capture_id) -> str:
    return conn.execute(
        "SELECT status FROM captures WHERE id = ?", (capture_id,)
    ).fetchone()["status"]


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("start", STATUSES)
def test_state_machine(conn, action, start):
    capture_id = _insert_capture(conn, status=start)
    allowed_from, end = ALLOWED[action]

    moved = ACTIONS[action](conn, capture_id)

    if start in allowed_from:
        assert moved and _status(conn, capture_id) == end
    else:
        assert not moved and _status(conn, capture_id) == start


def test_refused_failure_is_not_logged(conn):
    capture_id = _insert_capture(conn, status="written")
    mark_failed(conn, capture_id, reason="x", source_type=None)
    assert conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0] == 0


def test_full_journey_queued_pulled_written(conn):
    capture_id = _add(conn)
    assert mark_queued(
        conn, capture_id, source_type="web_article", tldr="x", full_text="x"
    )
    assert pending_captures(conn)  # waiting for the Vault

    assert mark_pulled(conn, capture_id)
    assert mark_written(conn, capture_id)
    assert mark_written(conn, capture_id)  # the laptop retried its ack: still fine

    assert pending_captures(conn) == []
    assert find_successful(conn, URL) is not None  # written still counts as captured
