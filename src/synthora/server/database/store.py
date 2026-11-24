import sqlite3
from pathlib import Path
from synthora.server.schemas import Capture

# ISO-8601 UTC with milliseconds; SQLite has no datetime type, so we store text.
_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"

_V1 = f"""
CREATE TABLE captures (
    id               INTEGER PRIMARY KEY,
    kind             TEXT    NOT NULL CHECK (kind IN ('url', 'image')),
    value            TEXT    NOT NULL,          -- the URL, or the image file_id
    source_type      TEXT,                      -- NULL until classified
    status           TEXT    NOT NULL DEFAULT 'processing'
                     CHECK (status IN ('processing', 'queued', 'pulled', 'written', 'failed')),
    comment          TEXT,
    tldr             TEXT,
    full_text        TEXT,                      -- FR-9: re-process without re-fetching
    chat_id          INTEGER NOT NULL,
    user_message_id  INTEGER NOT NULL,
    reply_message_id INTEGER,                   -- the bot's ack/result/failure message
    created_at       TEXT    NOT NULL DEFAULT {_NOW},
    updated_at       TEXT    NOT NULL DEFAULT {_NOW}
) STRICT;

CREATE TABLE failures (
    id          INTEGER PRIMARY KEY,
    capture_id  INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    reason      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT {_NOW},
    resolved_at TEXT                            -- set when a reply completes it (FR-22)
) STRICT;

-- 6.2 Repeat Capture: exact URL lookup. Partial index: only URL rows are indexed,
-- so queries must say `kind = 'url'` to use it.
CREATE INDEX ix_captures_url ON captures(value) WHERE kind = 'url';
-- 6.3 Failure reply: find the Capture behind a bot message.
CREATE INDEX ix_captures_reply ON captures(chat_id, reply_message_id);
-- 6.4 /status: list by status.
CREATE INDEX ix_captures_status ON captures(status);
CREATE INDEX ix_failures_capture ON failures(capture_id);
"""

# Append new scripts here; never edit one that has already run on a real database.
MIGRATIONS: list[str] = [_V1]


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database, turn on the settings we rely on, and migrate it."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["status"]
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Run every migration newer than the database's user_version, each atomically."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        try:
            conn.executescript(
                f"BEGIN; {script} PRAGMA user_version = {version}; COMMIT;"
            )
        except sqlite3.Error:
            conn.rollback()
            raise


SUCCESSFUL = ("queued", "pulled", "written")


def add_capture(
    conn: sqlite3.Connection,
    capture: Capture,
    *,
    chat_id: int,
    user_message_id: int,
    reply_message_id: int,
) -> int:
    """Record a new Capture as 'processing' and return its id."""
    with conn:
        cur = conn.execute(
            """INSERT INTO captures
                   (kind, value, comment, chat_id, user_message_id, reply_message_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                capture.kind,
                capture.value,
                capture.comment,
                chat_id,
                user_message_id,
                reply_message_id,
            ),
        )
    return cur.lastrowid


def mark_queued(
    conn: sqlite3.Connection,
    capture_id: int,
    *,
    source_type: str,
    tldr: str,
    full_text: str,
) -> None:
    """The graph succeeded: keep the result and wait for the laptop to pull it."""
    with conn:
        cur = conn.execute(
            f"""UPDATE captures
                SET status = 'queued', source_type = ?, tldr = ?, full_text = ?,
                    updated_at = {_NOW}
                WHERE id = ? AND status = 'processing'""",
            (source_type, tldr, full_text, capture_id),
        )
    return cur.rowcount == 1


def mark_failed(
    conn: sqlite3.Connection, capture_id: int, *, reason: str, source_type: str | None
) -> None:
    """The graph failed: flag the Capture and log why, in one transaction."""
    with conn:
        cur = conn.execute(
            f"""UPDATE captures SET status = 'failed', source_type = ?, updated_at = {_NOW}
                WHERE id = ? AND status = 'processing'""",
            (source_type, capture_id),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO failures (capture_id, reason) VALUES (?, ?)",
            (capture_id, reason),
        )
    return True


def mark_pulled(conn: sqlite3.Connection, capture_id: int) -> bool:
    """The laptop took it: queued → pulled."""
    with conn:
        cur = conn.execute(
            f"""UPDATE captures SET status = 'pulled', updated_at = {_NOW}
                WHERE id = ? AND status = 'queued'""",
            (capture_id,),
        )
    return cur.rowcount == 1


def mark_written(conn: sqlite3.Connection, capture_id: int) -> bool:
    """The laptop's ack: pulled → written. Acking twice is harmless (idempotent)."""
    with conn:
        conn.execute(
            f"""UPDATE captures SET status = 'written', updated_at = {_NOW}
                WHERE id = ? AND status = 'pulled'""",
            (capture_id,),
        )
    row = conn.execute(
        "SELECT status FROM captures WHERE id = ?", (capture_id,)
    ).fetchone()
    return row is not None and row["status"] == "written"


def find_successful(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    """FR-23: the first successful Capture of exactly this URL, if any."""
    return conn.execute(
        f"""SELECT * FROM captures
            WHERE kind = 'url' AND value = ? AND status IN {SUCCESSFUL}
            ORDER BY id LIMIT 1""",
        (url,),
    ).fetchone()


def find_failed_by_reply(
    conn: sqlite3.Connection, chat_id: int, reply_message_id: int
) -> list[sqlite3.Row]:
    """FR-22: the failed Captures behind one bot message."""
    return conn.execute(
        """SELECT * FROM captures
           WHERE chat_id = ? AND reply_message_id = ? AND status = 'failed'
           ORDER BY id""",
        (chat_id, reply_message_id),
    ).fetchall()


def complete_failed(
    conn: sqlite3.Connection, capture_id: int, *, tldr: str, full_text: str
) -> bool:
    """FR-22: failed → queued, using text the user supplied.

    Returns False (and changes nothing) if the Capture wasn't failed.
    """
    with conn:
        cur = conn.execute(
            f"""UPDATE captures
                SET status = 'queued', tldr = ?, full_text = ?, updated_at = {_NOW}
                WHERE id = ? AND status = 'failed'""",
            (tldr, full_text, capture_id),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            f"UPDATE failures SET resolved_at = {_NOW} WHERE capture_id = ? AND resolved_at IS NULL",
            (capture_id,),
        )
    return True


PENDING = ("queued", "pulled")  # captured, but not yet written to the Vault


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM captures GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


def pending_captures(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """FR-24: Captures waiting for the laptop, oldest first."""
    return conn.execute(
        f"SELECT * FROM captures WHERE status IN {PENDING} ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()


def recent_failures(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """FR-24: Failed Captures, newest first, hiding any URL that later succeeded."""
    return conn.execute(
        f"""SELECT c.id, c.value, c.source_type, f.reason, f.created_at
            FROM failures AS f
            JOIN captures AS c ON c.id = f.capture_id
            WHERE c.status = 'failed'
              AND NOT EXISTS (
                  SELECT 1 FROM captures AS later
                  WHERE later.kind = 'url'
                    AND later.value = c.value
                    AND later.status IN {SUCCESSFUL}
              )
            ORDER BY f.id DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
