"""Owner cancels or completes a booking from /admin: the client is told."""

import asyncio
import types

import pytest

from conftest import MONDAY

import bookings
import masters

OWNER_ID = 100   # owner of business 1 in conftest
CLIENT_ID = 777


class Bot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text))


def press(data, bot):
    import bot as bot_module

    replies, edits = [], []

    async def answer(*args, **kwargs):
        pass

    async def reply_text(text, **kwargs):
        replies.append(text)

    async def edit_message_text(text, **kwargs):
        edits.append(text)

    owner = types.SimpleNamespace(id=OWNER_ID, full_name="Власник")
    query = types.SimpleNamespace(
        data=data, answer=answer, from_user=owner,
        message=types.SimpleNamespace(reply_text=reply_text),
        edit_message_text=edit_message_text,
    )
    update = types.SimpleNamespace(
        callback_query=query, effective_user=owner,
        effective_chat=types.SimpleNamespace(id=OWNER_ID),
    )
    context = types.SimpleNamespace(user_data={}, bot=bot)

    asyncio.run(bot_module.button_handler(update, context))
    return replies, edits


@pytest.fixture
def booking(business, frozen_now):
    """Стрижка (service 2) з майстринею Анною, Пн 10:00."""
    anna = masters.get_or_create_master(1, "Анна")["id"]
    masters.set_service_master(1, 2, anna)
    customer_id = bookings.get_or_create_customer(1, CLIENT_ID, "Клієнтка")
    return bookings.create_booking(1, customer_id, 2, MONDAY, "10:00")


def status(booking_id):
    import database

    conn = database.get_connection()
    row = conn.execute(
        "SELECT status FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()
    conn.close()
    return row["status"]


def test_owner_cancel_notifies_the_client(booking):
    bot = Bot()

    _, edits = press(f"admin_delete_{booking}", bot)

    assert status(booking) == "cancelled"
    [(chat_id, text)] = bot.sent
    assert chat_id == CLIENT_ID
    assert "«Салон» скасував ваш запис" in text
    assert "Стрижка\n👤 Майстер: Анна\n📅 2026-10-05\n🕒 10:00" in text
    assert "клієнта сповіщено" in edits[-1]
    # Час знову вільний.
    assert "10:00" in bookings.get_available_times(1, MONDAY, 2)


def test_second_tap_does_not_notify_again(booking):
    bot = Bot()
    press(f"admin_delete_{booking}", bot)

    replies, _ = press(f"admin_delete_{booking}", bot)

    assert len(bot.sent) == 1
    assert "вже скасовано" in replies[-1]


def test_blocked_client_does_not_stop_the_cancel(booking):
    _, edits = press(f"admin_delete_{booking}", Bot(fail=True))

    assert status(booking) == "cancelled"
    assert "Не вдалося сповістити клієнта" in edits[-1]


def test_other_owner_cannot_cancel(booking, db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO businesses (id, owner_telegram_id, name, slug) "
        "VALUES (2, 200, 'Інший', 'inshyi')"
    )
    conn.commit()
    conn.close()

    import bot as bot_module
    stranger_bot = Bot()
    replies, edits = [], []

    async def answer(*args, **kwargs):
        pass

    async def reply_text(text, **kwargs):
        replies.append(text)

    stranger = types.SimpleNamespace(id=200, full_name="Чужий")
    query = types.SimpleNamespace(
        data=f"admin_delete_{booking}", answer=answer, from_user=stranger,
        message=types.SimpleNamespace(reply_text=reply_text),
        edit_message_text=None,
    )
    asyncio.run(bot_module.button_handler(
        types.SimpleNamespace(
            callback_query=query, effective_user=stranger,
            effective_chat=types.SimpleNamespace(id=200),
        ),
        types.SimpleNamespace(user_data={}, bot=stranger_bot),
    ))

    assert status(booking) == "confirmed"
    assert stranger_bot.sent == []
    assert "не знайдено" in replies[-1]


def test_client_can_no_longer_cancel_a_completed_booking(booking):
    press(f"admin_complete_{booking}", Bot())

    assert not bookings.cancel_booking(booking, 1)
    assert status(booking) == "completed"


def test_completed_message_names_the_master(booking):
    bot = Bot()
    press(f"admin_complete_{booking}", bot)

    assert "👤 Майстер: Анна" in bot.sent[-1][1]
