"""The runner's loop: the real queue and store, fake Vault, Cartographer and git."""

import asyncio

import pytest

from synthora import store
from synthora.queue import LocalQueue
from synthora.runner.run import run_once
from synthora.domain import Capture, Distillation

IDS = {"chat_id": 1, "user_message_id": 10, "reply_message_id": 11}


def gist(tldr: str = "x") -> Distillation:
    return Distillation(tldr=tldr, key_ideas=["An idea."], notable_quotes=[])


class FakeVault:
    def __init__(self) -> None:
        self.notes: dict[str, str] = {}

    def create(self, path: str, body: str) -> None:
        if path in self.notes:
            raise FileExistsError(path)
        self.notes[path] = body

    def read(self, path: str) -> str:
        return self.notes[path]


class FakeCartographer:
    """Answers each Source with a scripted final state; records the titles it was given."""

    def __init__(
        self, *, deferred_for: set[str] = frozenset(), crash_for: set[str] = frozenset()
    ):
        self.seen: list[str] = []
        self.deferred_for, self.crash_for = deferred_for, crash_for

    async def __call__(self, message) -> dict:
        title = message.content.split("<title>")[1].split("</title>")[0]
        self.seen.append(title)
        if title in self.crash_for:
            raise RuntimeError("the Content Server died")
        if title in self.deferred_for:
            return {
                "stopped": "deferred",
                "deferred": ["Concepts/RAG.md"],
                "created": [],
            }
        return {"stopped": "done", "deferred": [], "created": []}


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    yield c
    c.close()


def queue_capture(conn, url, title, linked=()):
    cid = store.add_capture(conn, Capture(kind="url", value=url), **IDS)
    store.mark_queued(
        conn,
        cid,
        source_type="web_article",
        title=title,
        distillation=gist(title),
        full_text="text",
        linked=[
            store.LinkedQueued(
                url=u,
                source_type="github_repo",
                title=t,
                distillation=gist(t),
                full_text="readme",
            )
            for u, t in linked
        ],
    )
    return cid


def run(conn, vault, cartographer, commits):
    return asyncio.run(run_once(LocalQueue(conn), vault, cartographer, commits.append))


def status(conn, cid):
    return conn.execute("SELECT status FROM captures WHERE id = ?", (cid,)).fetchone()[
        "status"
    ]


def test_a_capture_is_noted_mapped_committed_and_acked(conn):
    cid = queue_capture(conn, "https://a.example", "Agents")
    vault, carto, commits = FakeVault(), FakeCartographer(), []
    (result,) = run(conn, vault, carto, commits)
    assert result.outcome == "written" and result.note == "Sources/Agents.md"
    assert "Sources/Agents.md" in vault.notes
    assert carto.seen == ["Agents"]  # the Cartographer cites the note's real name
    assert commits == ["synthora: Agents"]
    assert status(conn, cid) == "written"


def test_a_post_and_its_links_are_all_written_in_one_run(conn):
    queue_capture(
        conn,
        "https://post.example",
        "A post",
        linked=[("https://github.com/a", "Repo A"), ("https://github.com/b", "Repo B")],
    )
    vault, carto, commits = FakeVault(), FakeCartographer(), []
    results = run(conn, vault, carto, commits)
    assert [r.outcome for r in results] == ["written"] * 3
    assert carto.seen == ["A post", "Repo A", "Repo B"]  # the post first
    assert "saved_via: '[[A post]]'" in vault.notes["Sources/Repo A.md"]


def test_a_deferred_capture_is_committed_but_not_acked(conn):
    cid = queue_capture(conn, "https://a.example", "Agents")
    results = run(
        conn, FakeVault(), FakeCartographer(deferred_for={"Agents"}), commits := []
    )
    assert results[0].outcome == "deferred" and results[0].waiting_for == [
        "Concepts/RAG.md"
    ]
    assert commits == ["synthora: Agents (deferred)"]  # what it did write is kept
    assert status(conn, cid) == "pulled"  # offered again next run


def test_a_deferred_post_holds_back_its_links(conn):
    queue_capture(
        conn,
        "https://post.example",
        "A post",
        linked=[("https://github.com/a", "Repo A")],
    )
    carto = FakeCartographer(deferred_for={"A post"})
    run(conn, FakeVault(), carto, [])
    assert carto.seen == [
        "A post"
    ]  # Repo A waits: its saved_via would point at an unfinished post


def test_one_crash_does_not_stop_the_others(conn):
    bad = queue_capture(conn, "https://bad.example", "Bad")
    good = queue_capture(conn, "https://good.example", "Good")
    results = run(conn, FakeVault(), FakeCartographer(crash_for={"Bad"}), [])
    assert [r.outcome for r in results] == ["failed", "written"]
    assert "the Content Server died" in results[0].detail
    assert status(conn, bad) == "pulled" and status(conn, good) == "written"


def test_running_again_after_a_crash_finishes_the_job(conn):
    cid = queue_capture(conn, "https://a.example", "Agents")
    vault = FakeVault()
    run(
        conn, vault, FakeCartographer(crash_for={"Agents"}), []
    )  # the note is written, then a crash
    (result,) = run(conn, vault, FakeCartographer(), commits := [])
    assert result.outcome == "written" and result.note == "Sources/Agents.md"
    assert list(vault.notes) == ["Sources/Agents.md"]  # reused, not 'Agents (2)'
    assert commits == ["synthora: Agents"] and status(conn, cid) == "written"


def test_nothing_queued_is_a_quiet_no_op(conn):
    assert (
        run(conn, FakeVault(), FakeCartographer(), commits := []) == []
        and commits == []
    )
