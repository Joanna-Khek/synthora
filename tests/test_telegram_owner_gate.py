"""Tests for the owner-only gate (PRD FR-2).

The bot must respond only to the owner's Telegram user ID and silently ignore
everyone else. These tests run offline: they build fake Updates and never
contact Telegram.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop, TypeHandler

from synthora.server.bot.app import build_app, make_owner_gate

OWNER_ID = 111
STRANGER_ID = 999


def make_update(user_id: int) -> Update:
    """Build a fake private-chat text Update sent by ``user_id``.

    Only the fields the gate reads are realistic; nothing is sent to Telegram.
    """
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type=Chat.PRIVATE),
            from_user=User(id=user_id, first_name="Test", is_bot=False),
            text="hi",
        ),
    )


def test_owner_is_let_through():
    """An Update from the owner passes the gate (no exception is raised)."""
    asyncio.run(
        make_owner_gate(OWNER_ID)(make_update(OWNER_ID), None)
    )  # no exception = allowed


def test_stranger_is_stopped():
    """An Update from anyone else raises ApplicationHandlerStop, so no later handler runs."""
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(make_owner_gate(OWNER_ID)(make_update(STRANGER_ID), None))


def test_gate_runs_before_every_handler():
    """The gate is registered so it runs before all other handlers.

    python-telegram-bot runs handler groups from lowest to highest number, so the
    lowest group must be negative (below the default group 0) and must start with
    a catch-all TypeHandler. This catches the gate being moved, dropped or
    registered in the wrong group, which the other two tests would not notice
    because they call the gate function directly.
    """
    app = build_app("123:FAKE", OWNER_ID)
    first_group = min(app.handlers)
    assert first_group < 0
    assert isinstance(app.handlers[first_group][0], TypeHandler)
