"""Two independent businesses on one bot: nothing of one may leak into,
be modified through, or be counted in the other."""

import asyncio
import sqlite3
import types

import pytest

from conftest import MONDAY

import bookings
import business_context
import database
import masters

A, B = 1, 2
SHARED_CLIENT = 777  # the same Telegram user is a client of both


@pytest.fixture
def two_businesses(business, db):
    """A = conftest business (owner 100, services 1 and 2, Pro).
    B = owner 200, service 10 (60 min), Pro, open Mon-Sat 09-18."""
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO businesses (id, owner_telegram_id, name, slug, pro_until) "
        "VALUES (2, 200, 'Барбер Б', 'barber-b', '2099-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO services (id, business_id, name, price, duration) "
        "VALUES (10, 2, 'Стрижка Б', 250, 60)"
    )
    conn.commit()
    conn.close()
    for weekday in range(6):
        db.set_working_hours(B, weekday, "09:00", "18:00", 1)
    return A, B


def customer(business_id, telegram_id=SHARED_CLIENT, name="Клієнт"):
    return bookings.get_or_create_customer(business_id, telegram_id, name)


def book(business_id, service_id, time="10:00", telegram_id=SHARED_CLIENT):
    return bookings.create_booking(
        business_id, customer(business_id, telegram_id), service_id,
        MONDAY, time
    )


# ---------- data layer ----------

def test_same_telegram_user_is_a_separate_customer_per_business(two_businesses):
    customer(A, name="Іван")
    customer(B, name="Ivan")
    bookings.set_customer_phone(A, SHARED_CLIENT, "+380111")

    assert bookings.get_customer(A, SHARED_CLIENT)["name"] == "Іван"
    assert bookings.get_customer(B, SHARED_CLIENT)["name"] == "Ivan"
    assert bookings.get_customer(B, SHARED_CLIENT)["phone"] is None


def test_booking_lists_and_counts_are_per_business(two_businesses, frozen_now):
    assert book(A, 2)
    assert book(B, 10)

    assert [row["service"] for row in bookings.get_business_bookings(A)] == ["Стрижка"]
    assert [row["service"] for row in bookings.get_business_bookings(B)] == ["Стрижка Б"]
    assert len(bookings.get_customer_bookings(A, customer(A))) == 1
    assert len(bookings.get_customer_bookings(B, customer(B))) == 1


def test_one_business_does_not_block_anothers_time(two_businesses, frozen_now):
    assert book(A, 1, "10:00")
    assert "10:00" in bookings.get_available_times(B, MONDAY, 10)
    assert bookings.booking_time_error(B, MONDAY, "10:00", 10) is None
    assert book(B, 10, "10:00")


def test_free_daily_limit_is_counted_per_business(two_businesses, frozen_now, db):
    conn = db.get_connection()
    conn.execute("UPDATE businesses SET pro_until = NULL")
    conn.commit()
    conn.close()

    assert book(A, 2)
    assert book(B, 10)


def test_create_booking_rejects_another_business_service(two_businesses, frozen_now):
    assert book(B, 1) is None           # service 1 belongs to A
    assert bookings.get_business_bookings(B) == []


def test_create_booking_rejects_another_business_customer(two_businesses, frozen_now):
    a_customer = customer(A)
    assert bookings.create_booking(B, a_customer, 10, MONDAY, "10:00") is None


def test_owner_actions_cannot_touch_another_business_booking(two_businesses, frozen_now):
    a_booking = book(A, 2)

    assert not bookings.cancel_booking(a_booking, B)
    assert not bookings.mark_booking_completed(a_booking, B)
    assert not bookings.reschedule_booking(a_booking, B, MONDAY, "15:00")
    assert bookings.get_booking_with_customer(a_booking, B) is None

    row = bookings.get_booking_with_customer(a_booking, A)
    assert (row["date"], row["time"]) == (MONDAY, "10:00")
    assert bookings.get_business_bookings(A)[0]["id"] == a_booking


def test_services_schedules_faq_and_masters_are_per_business(two_businesses, db):
    assert [s["id"] for s in business_context.get_business_services(A)] == [1, 2]
    assert [s["id"] for s in business_context.get_business_services(B)] == [10]
    assert business_context.get_service_by_id(1, B) is None

    db.set_working_hours(A, 0, "07:00", "08:00", 1)
    assert [row["start_time"] for row in db.get_working_hours(B) if row["weekday"] == 0] == ["09:00"]

    business_context.add_faq_item(A, "Питання А", "Відповідь А")
    assert business_context.get_faq_items(B) == []
    item_id = business_context.get_faq_items(A)[0]["id"]
    business_context.delete_faq_item(item_id, B)
    assert len(business_context.get_faq_items(A)) == 1

    masters.get_or_create_master(A, "Оля")
    assert masters.get_masters(B) == []


# ---------- database-level guarantees (migration 0005 triggers) ----------

def _insert(db, sql, params=()):
    conn = db.get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("sql", [
    "INSERT INTO bookings (business_id, service_id, booking_date, booking_time) "
    "VALUES (2, 1, '2026-10-05', '10:00')",
    "INSERT INTO working_hours (business_id, service_id, weekday, is_open) "
    "VALUES (2, 1, 0, 1)",
])
def test_database_rejects_cross_business_rows(two_businesses, db, sql):
    with pytest.raises(sqlite3.IntegrityError, match="another business"):
        _insert(db, sql)


def test_database_rejects_cross_business_customer_and_master(two_businesses, db):
    a_customer = customer(A)
    with pytest.raises(sqlite3.IntegrityError, match="another business"):
        _insert(
            db,
            "INSERT INTO bookings (business_id, customer_id, service_id, "
            "booking_date, booking_time) VALUES (2, ?, 10, '2026-10-05', '10:00')",
            (a_customer,)
        )

    a_master = masters.get_or_create_master(A, "Оля")["id"]
    with pytest.raises(sqlite3.IntegrityError, match="another business"):
        _insert(db, "UPDATE services SET master_id = ? WHERE id = 10", (a_master,))


def test_database_rejects_moving_a_booking_to_another_business(
    two_businesses, db, frozen_now
):
    a_booking = book(A, 2)
    with pytest.raises(sqlite3.IntegrityError, match="another business"):
        _insert(db, "UPDATE bookings SET business_id = 2 WHERE id = ?", (a_booking,))


# ---------- bot handlers ----------

class Message:
    def __init__(self, text=None):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def _user(telegram_id=SHARED_CLIENT):
    return types.SimpleNamespace(id=telegram_id, full_name="Клієнт")


@pytest.fixture
def quiet_bot(monkeypatch):
    import bot

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(bot, "on_start", noop)
    monkeypatch.setattr(bot, "on_booking_created", noop)
    return bot


def test_opening_another_business_link_drops_unfinished_booking_state(
    two_businesses, quiet_bot
):
    bot = quiet_bot
    user_data = {
        "client_business_id": A,
        "booking_business_id": A,
        "service_id": 1,
        "service_name": "Фарбування",
        "date": MONDAY,
        "time": "10:00",
        "pending_cancel_ids": [5],
        "pending_cancel_business_id": A,
        "pending_reschedule": {"booking_id": 5, "business_id": A},
        "ai_state": {"service": 1},
    }
    update = types.SimpleNamespace(message=Message(), effective_user=_user())
    context = types.SimpleNamespace(args=["barber-b"], user_data=user_data, bot=Bot())

    asyncio.run(bot.start(update, context))

    assert user_data == {"client_business_id": B}


def test_finalize_booking_refuses_a_service_from_another_business(
    two_businesses, quiet_bot, frozen_now, monkeypatch
):
    bot = quiet_bot
    monkeypatch.setattr(bookings, "now_local", lambda: frozen_now)
    business_b = business_context.get_business_by_id(B)
    context = types.SimpleNamespace(user_data={"service_id": 1}, bot=Bot())
    update = types.SimpleNamespace(
        effective_user=_user(), effective_chat=types.SimpleNamespace(id=SHARED_CLIENT)
    )

    asyncio.run(bot.finalize_booking(
        update, context, business_b, 1, "Фарбування", MONDAY, "10:00", "ok"
    ))

    assert bookings.get_business_bookings(B) == []
    assert "Сесію бронювання втрачено" in context.bot.sent[-1][1]


def test_stale_confirm_button_after_switching_business_books_nothing(
    two_businesses, quiet_bot, frozen_now
):
    """Client picks A's service and a time, opens B's link, taps
    «Записатися» in B, then taps the old «✅ Підтвердити» from A."""
    bot = quiet_bot
    user_data = {
        "client_business_id": A, "booking_business_id": A,
        "service_id": 1, "service_name": "Фарбування",
        "date": MONDAY, "time": "10:00",
    }
    context = types.SimpleNamespace(args=["barber-b"], user_data=user_data, bot=Bot())
    asyncio.run(bot.start(
        types.SimpleNamespace(message=Message(), effective_user=_user()), context
    ))

    def press(data):
        async def answer(*args, **kwargs):
            pass

        message = Message()
        query = types.SimpleNamespace(
            data=data, answer=answer, message=message, from_user=_user()
        )
        update = types.SimpleNamespace(
            callback_query=query, effective_user=_user(),
            effective_chat=types.SimpleNamespace(id=SHARED_CLIENT),
        )
        asyncio.run(bot.button_handler(update, context))
        return message.replies

    press("book")
    replies = press("confirm")

    assert "Дані запису втрачено" in replies[-1]
    assert bookings.get_business_bookings(A) == []
    assert bookings.get_business_bookings(B) == []


def test_client_faq_button_for_another_business_is_refused(two_businesses, db):
    import client_faq

    business_context.add_faq_item(B, "Секрет Б", "Відповідь Б")
    answers, edits = [], []

    async def answer(text=None, **kwargs):
        answers.append(text)

    async def edit_message_text(text, **kwargs):
        edits.append(text)

    query = types.SimpleNamespace(
        data=f"cfaq_{B}_list", answer=answer,
        edit_message_text=edit_message_text,
        from_user=types.SimpleNamespace(id=SHARED_CLIENT), message=Message(),
    )
    context = types.SimpleNamespace(user_data={"client_business_id": A})

    asyncio.run(client_faq.client_faq_callback(
        types.SimpleNamespace(callback_query=query), context
    ))

    assert answers == ["⚠️ Бізнес не знайдено."]
    assert edits == []

    # The same button works for a client who is actually in business B.
    context.user_data["client_business_id"] = B
    query.data = f"cfaq_{B}_{business_context.get_faq_items(B)[0]['id']}"
    asyncio.run(client_faq.client_faq_callback(
        types.SimpleNamespace(callback_query=query), context
    ))
    assert "Відповідь Б" in edits[-1]
