"""Tests for CapturePipeline, through its interface only: run() and complete().

A real in-memory SQLite database and the fake Tools: no Telegram, no network.
"""

import asyncio

import pytest
from fakes import ARTICLE_URL, POST_URL, BrokenDistillTools, BrokenReadTools, FakeTools

from synthora import store
from synthora.capture.pipeline import (
    CapturePipeline,
    Failed,
    MessageIds,
    Repeat,
    StepCount,
    StepDone,
    Succeeded,
)
from synthora.domain import Capture, SourceType

IDS = MessageIds(chat_id=1, user_message_id=10, reply_message_id=11)


@pytest.fixture
def db():
    conn = store.connect(":memory:")
    yield conn
    conn.close()


def run(db, tools, value=ARTICLE_URL, kind="url", events=None):
    async def on_progress(event):
        if events is not None:
            events.append(event)

    pipeline = CapturePipeline(db, tools)
    return asyncio.run(pipeline.run(Capture(kind=kind, value=value), IDS, on_progress))


def only_row(db):
    (row,) = db.execute("SELECT * FROM captures").fetchall()
    return row


# ---------------------------------------------------------------- run()
def test_success_is_queued_with_the_tldr_and_full_text(db):
    outcome = run(db, FakeTools())

    assert isinstance(outcome, Succeeded)
    assert outcome.distillation.tldr == f"TL;DR of {ARTICLE_URL}"
    row = only_row(db)
    assert row["status"] == "queued"
    assert row["source_type"] == SourceType.WEB_ARTICLE
    assert row["tldr"] == f"TL;DR of {ARTICLE_URL}"
    assert row["full_text"] == "Some text."
    assert (row["chat_id"], row["user_message_id"], row["reply_message_id"]) == (
        1,
        10,
        11,
    )


def test_read_failure_is_a_failed_capture_with_its_reason(db):
    outcome = run(db, BrokenReadTools())

    assert isinstance(outcome, Failed)
    assert "HTTP 404" in outcome.reason
    assert outcome.source_type is SourceType.WEB_ARTICLE
    assert only_row(db)["status"] == "failed"
    (failure,) = db.execute("SELECT reason FROM failures").fetchall()
    assert "HTTP 404" in failure["reason"]


def test_distill_failure_is_a_failed_capture(db):
    outcome = run(db, BrokenDistillTools())
    assert isinstance(outcome, Failed)
    assert "declined" in outcome.reason
    assert only_row(db)["status"] == "failed"


def test_a_repeat_capture_runs_nothing(db):
    run(db, FakeTools())
    tools = FakeTools()

    outcome = run(db, tools)

    assert isinstance(outcome, Repeat)
    assert outcome.tldr == f"TL;DR of {ARTICLE_URL}"
    assert tools.calls == []
    assert db.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 1


def test_a_failed_url_is_tried_again(db):
    run(db, BrokenReadTools())
    assert isinstance(run(db, FakeTools()), Succeeded)


def test_a_bug_marks_the_row_failed_and_is_raised(db):
    class Buggy(FakeTools):
        async def distill(self, source):
            raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        run(db, Buggy())

    assert only_row(db)["status"] == "failed"
    (failure,) = db.execute("SELECT reason FROM failures").fetchall()
    assert failure["reason"] == "bug: ZeroDivisionError"


def test_linked_resources_and_notes_reach_the_outcome(db):
    class LowCeiling(FakeTools):
        max_linked = 1

    outcome = run(db, LowCeiling(picks=[1, 2]), value=POST_URL)

    assert isinstance(outcome, Succeeded)
    assert len(outcome.linked) == 1
    assert "safety ceiling of 1" in outcome.notes[0]


def test_each_distilled_link_is_queued_as_its_own_row(db):
    outcome = run(db, FakeTools(picks=[1, 2]), value=POST_URL)
    assert isinstance(outcome, Succeeded)
    post = db.execute("SELECT id FROM captures WHERE parent_id IS NULL").fetchone()[
        "id"
    ]
    children = db.execute(
        "SELECT value, status, title FROM captures WHERE parent_id = ? ORDER BY id",
        (post,),
    ).fetchall()
    distilled = [item.url for item in outcome.linked if hasattr(item, "distillation")]
    assert [c["value"] for c in children] == distilled
    assert all(c["status"] == "queued" and c["title"] for c in children)


def test_progress_reports_each_step_then_the_counters(db):
    events = []
    run(db, FakeTools(picks=[1, 2]), value=POST_URL, events=events)

    steps = [e.node for e in events if isinstance(e, StepDone)]
    assert steps == ["classify", "read", "pick_links", "read_linked", "distill"]
    counts = [e for e in events if isinstance(e, StepCount)]
    assert {e.step for e in counts} == {"read_linked", "distill"}


# ---------------------------------------------------------------- complete()
def failed_row(db):
    run(db, BrokenReadTools())
    return only_row(db)


def test_complete_queues_a_failed_capture_from_pasted_text(db):
    row = failed_row(db)

    outcome = asyncio.run(
        CapturePipeline(db, FakeTools()).complete(row, "Pasted text.")
    )

    assert isinstance(outcome, Succeeded)
    assert outcome.source.url == ARTICLE_URL
    row = only_row(db)
    assert row["status"] == "queued"
    assert row["full_text"] == "Pasted text."
    (failure,) = db.execute("SELECT resolved_at FROM failures").fetchall()
    assert failure["resolved_at"] is not None


def test_complete_leaves_the_capture_failed_when_distilling_fails(db):
    row = failed_row(db)

    outcome = asyncio.run(
        CapturePipeline(db, BrokenDistillTools()).complete(row, "Pasted text.")
    )

    assert isinstance(outcome, Failed)
    assert "declined" in outcome.reason
    assert only_row(db)["status"] == "failed"


def test_completing_twice_is_refused(db):
    row = failed_row(db)
    pipeline = CapturePipeline(db, FakeTools())
    asyncio.run(pipeline.complete(row, "Pasted text."))

    outcome = asyncio.run(pipeline.complete(row, "Pasted again."))

    assert isinstance(outcome, Failed)
    assert only_row(db)["full_text"] == "Pasted text."
