"""The laptop's queue, on a real in-memory database."""

from datetime import date

import pytest

from synthora import store
from synthora.queue import LocalQueue, QueueError
from synthora.domain import Capture, Distillation, SourceType

IDS = {"chat_id": 1, "user_message_id": 10, "reply_message_id": 11}


def gist(tldr: str = "x") -> Distillation:
    return Distillation(tldr=tldr, key_ideas=["An idea."], notable_quotes=[])


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    yield c
    c.close()


def add_post_with_links(
    conn, links=("https://github.com/a", "https://github.com/b")
) -> int:
    post = store.add_capture(
        conn,
        Capture(kind="url", value="https://linkedin.com/posts/x", comment="for later"),
        **IDS,
    )
    store.mark_queued(
        conn,
        post,
        source_type="linkedin_post",
        title="A post",
        distillation=gist("post"),
        full_text="post text",
        linked=[
            store.LinkedQueued(
                url=u,
                source_type="github_repo",
                title=f"Repo {u[-1]}",
                distillation=gist(u),
                full_text="readme",
            )
            for u in links
        ],
    )
    return post


def test_a_queued_capture_comes_back_typed(conn):
    post = add_post_with_links(conn, links=())
    (item,) = LocalQueue(conn).pending()
    assert item.id == post and item.url == "https://linkedin.com/posts/x"
    assert item.source_type is SourceType.LINKEDIN_POST
    assert item.title == "A post" and item.comment == "for later"
    assert item.distillation == gist("post")
    assert item.captured == date.today() and item.parent_title is None


def test_links_wait_until_their_post_is_written(conn):
    post = add_post_with_links(conn)
    queue = LocalQueue(conn)
    assert [i.id for i in queue.pending()] == [post]  # the links aren't ready yet

    queue.claim(post)
    queue.ack(post)

    links = queue.pending()
    assert [i.url for i in links] == ["https://github.com/a", "https://github.com/b"]
    assert all(i.parent_title == "A post" for i in links)  # for saved_via


def test_a_claimed_item_comes_back_until_it_is_acked(conn):
    """At-least-once: a run that crashed (or was deferred) after claiming gets it again."""
    post = add_post_with_links(conn, links=())
    queue = LocalQueue(conn)
    queue.claim(post)
    assert [i.id for i in queue.pending()] == [post]
    queue.claim(post)  # the next run claims it again: harmless
    queue.ack(post)
    assert queue.pending() == []


def test_acking_twice_is_harmless_but_acking_unclaimed_is_refused(conn):
    post = add_post_with_links(conn, links=())
    queue = LocalQueue(conn)
    with pytest.raises(QueueError, match="never claimed"):
        queue.ack(post)
    queue.claim(post)
    queue.ack(post)
    queue.ack(post)  # the laptop retried its ack


def test_a_capture_from_before_v2_gets_a_tldr_only_distillation(conn):
    conn.execute(
        "INSERT INTO captures (kind, value, status, tldr, chat_id, user_message_id) "
        "VALUES ('url', 'https://old.example', 'queued', 'old gist', 1, 10)"
    )
    (item,) = LocalQueue(conn).pending()
    assert item.distillation.tldr == "old gist" and item.distillation.key_ideas == []
    assert item.title is None and item.source_type is None


def test_failed_and_processing_captures_are_never_offered(conn):
    store.mark_failed(
        conn,
        store.add_capture(conn, Capture(kind="url", value="https://f.x"), **IDS),
        reason="404",
        source_type=None,
    )
    store.add_capture(
        conn, Capture(kind="url", value="https://p.x"), **IDS
    )  # still processing
    assert LocalQueue(conn).pending() == []
