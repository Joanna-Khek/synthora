import sqlite3

import pytest

from synthora.store import MIGRATIONS, connect
from synthora.store import (
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
    stored_distillation,
    LinkedQueued,
)
from synthora.bot.status import build_status
from synthora.domain import Capture, Distillation


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


def gist(tldr: str = "x") -> Distillation:
    """A minimal Distillation whose TL;DR the test cares about."""
    return Distillation(tldr=tldr, key_ideas=["An idea."], notable_quotes=[])


def _add(conn, url=URL) -> int:
    return add_capture(conn, Capture(kind="url", value=url), **IDS)


def _queue(conn, capture_id: int, tldr: str = "x") -> bool:
    return mark_queued(
        conn,
        capture_id,
        source_type="web_article",
        title=None,
        distillation=gist(tldr),
        full_text="x",
    )


def test_successful_capture_is_found(conn):
    _queue(conn, _add(conn), "the gist")
    assert find_successful(conn, URL)["tldr"] == "the gist"


def test_failed_capture_does_not_count(conn):
    mark_failed(conn, _add(conn), reason="404", source_type=None)
    assert find_successful(conn, URL) is None


def test_capture_still_processing_does_not_count(conn):
    _add(conn)
    assert find_successful(conn, URL) is None


def test_match_is_exact(conn):
    _queue(conn, _add(conn))
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
    _queue(conn, ok)
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

    assert complete_failed(
        conn, capture_id, distillation=gist("the gist"), full_text="pasted"
    )

    assert find_successful(conn, URL)["tldr"] == "the gist"
    failure = conn.execute(
        "SELECT resolved_at FROM failures WHERE capture_id = ?", (capture_id,)
    ).fetchone()
    assert failure["resolved_at"] is not None


def test_only_failed_captures_can_be_completed(conn):
    capture_id = _add(conn)  # still 'processing'
    assert not complete_failed(conn, capture_id, distillation=gist(), full_text="x")
    status = conn.execute(
        "SELECT status FROM captures WHERE id = ?", (capture_id,)
    ).fetchone()
    assert status["status"] == "processing"


def test_pending_lists_only_queued_and_pulled(conn):
    _queue(conn, _add(conn, "https://a.com"))
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

    _queue(conn, _add(conn))
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
        _queue(conn, _add(conn, f"https://{i}.com"))
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
    "queue": _queue,
    "fail": lambda conn, cid: mark_failed(conn, cid, reason="x", source_type=None),
    "pull": mark_pulled,
    "write": mark_written,
    "complete": lambda conn, cid: complete_failed(
        conn, cid, distillation=gist(), full_text="x"
    ),
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
    assert _queue(conn, capture_id)
    assert pending_captures(conn)  # waiting for the Vault

    assert mark_pulled(conn, capture_id)
    assert mark_written(conn, capture_id)
    assert mark_written(conn, capture_id)  # the laptop retried its ack: still fine

    assert pending_captures(conn) == []
    assert find_successful(conn, URL) is not None  # written still counts as captured


# ---------------------------------------------------------------- 11.0 migration v2: the full distillation
def test_queued_capture_keeps_its_title_and_whole_distillation(conn):
    d = Distillation(
        tldr="The gist.", key_ideas=["One.", "Two."], notable_quotes=["A quote."]
    )
    capture_id = _add(conn)
    mark_queued(
        conn,
        capture_id,
        source_type="web_article",
        title="A Post: Part 1",
        distillation=d,
        full_text="...",
    )
    row = pending_captures(conn)[0]
    assert row["title"] == "A Post: Part 1"
    assert row["tldr"] == "The gist."  # still its own column, for /status and repeats
    assert stored_distillation(row) == d  # round-trips exactly


def test_completed_failure_keeps_the_distillation_too(conn):
    capture_id = _add(conn)
    mark_failed(conn, capture_id, reason="404", source_type=None)
    complete_failed(
        conn, capture_id, distillation=gist("pasted gist"), full_text="pasted"
    )
    assert stored_distillation(pending_captures(conn)[0]).tldr == "pasted gist"


def test_v1_database_is_migrated_and_old_rows_have_no_distillation(tmp_path):
    """Exactly your real database's situation: v1, with Captures already in it."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(f"BEGIN; {MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
    old.execute(
        "INSERT INTO captures (kind, value, status, tldr, chat_id, user_message_id) "
        "VALUES ('url', 'https://old.example', 'queued', 'old gist', 1, 10)"
    )
    old.commit()
    old.close()

    conn = connect(path)  # runs migration v2 on a database with real data
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    row = pending_captures(conn)[0]
    assert row["tldr"] == "old gist"  # existing data survives
    assert row["title"] is None and stored_distillation(row) is None


def test_the_database_refuses_a_distillation_that_is_not_json(conn):
    capture_id = _add(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE captures SET distillation = 'not json' WHERE id = ?", (capture_id,)
        )


# ---------------------------------------------------------------- 11.1 migration v3: linked Sources get their own rows
def link(url: str, tldr: str = "linked gist") -> LinkedQueued:
    return LinkedQueued(
        url=url,
        source_type="github_repo",
        title=f"Repo {url[-1]}",
        distillation=gist(tldr),
        full_text="readme",
    )


def test_linked_sources_are_queued_as_rows_pointing_at_their_post(conn):
    post = _add(conn)
    assert mark_queued(
        conn,
        post,
        source_type="linkedin_post",
        title="A post",
        distillation=gist("post gist"),
        full_text="post",
        linked=[link("https://github.com/a"), link("https://github.com/b")],
    )
    rows = conn.execute(
        "SELECT * FROM captures WHERE parent_id = ? ORDER BY id", (post,)
    ).fetchall()
    assert [r["value"] for r in rows] == [
        "https://github.com/a",
        "https://github.com/b",
    ]
    first = rows[0]
    assert first["status"] == "queued" and first["title"] == "Repo a"
    assert stored_distillation(first).tldr == "linked gist"
    assert (first["chat_id"], first["user_message_id"]) == (1, 10)  # from the post
    assert first["reply_message_id"] is None  # not reachable by a failure reply (FR-22)
    assert len(pending_captures(conn)) == 3  # /status counts the post and both links


def test_a_link_already_captured_is_not_queued_twice(conn):
    _queue(conn, _add(conn, "https://github.com/a"))  # captured on its own earlier
    post = _add(conn)
    mark_queued(
        conn,
        post,
        source_type="linkedin_post",
        title="A post",
        distillation=gist(),
        full_text="post",
        linked=[link("https://github.com/a"), link("https://github.com/b")],
    )
    children = conn.execute(
        "SELECT value FROM captures WHERE parent_id = ?", (post,)
    ).fetchall()
    assert [r["value"] for r in children] == ["https://github.com/b"]


def test_a_refused_post_queues_no_links(conn):
    post = _insert_capture(conn, status="failed")
    assert not mark_queued(
        conn,
        post,
        source_type="linkedin_post",
        title="A post",
        distillation=gist(),
        full_text="post",
        linked=[link("https://github.com/a")],
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM captures WHERE parent_id IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_a_reply_to_the_post_never_finds_its_links(conn):
    post = _add(conn)
    mark_queued(
        conn,
        post,
        source_type="linkedin_post",
        title="A post",
        distillation=gist(),
        full_text="post",
        linked=[link("https://github.com/a")],
    )
    conn.execute("UPDATE captures SET status = 'failed' WHERE parent_id = ?", (post,))
    assert find_failed_by_reply(conn, chat_id=1, reply_message_id=11) == []


def test_deleting_a_post_deletes_its_links(conn):
    post = _add(conn)
    mark_queued(
        conn,
        post,
        source_type="linkedin_post",
        title="A post",
        distillation=gist(),
        full_text="post",
        linked=[link("https://github.com/a")],
    )
    with conn:
        conn.execute("DELETE FROM captures WHERE id = ?", (post,))
    assert conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 0
