"""Per-master working hours: a service uses its own schedule, else its
master's, else the business default."""

import asyncio
import sqlite3
import types

import pytest
from telegram.ext import ConversationHandler

from conftest import MONDAY

import bookings
import business_context
import database
import masters
import schedule

THURSDAY = "2026-10-08"


@pytest.fixture
def salon(business):
    """Business default Mon-Sat 09-18 (conftest). Оля (services 3, 4)
    works Mon-Wed 10:00-14:00; Марина (service 5) has no own schedule."""
    olia = masters.get_or_create_master(1, "Оля")["id"]
    maryna = masters.get_or_create_master(1, "Марина")["id"]

    conn = database.get_connection()
    conn.execute(
        "INSERT INTO services (id, business_id, name, price, duration, master_id) "
        "VALUES (3, 1, 'Стрижка', 300, 60, ?), (4, 1, 'Фарбування', 900, 90, ?), "
        "(5, 1, 'Манікюр', 400, 60, ?)",
        (olia, olia, maryna)
    )
    conn.commit()
    conn.close()

    for weekday in range(7):
        if weekday <= 2:
            database.set_working_hours(1, weekday, "10:00", "14:00", 1, master_id=olia)
        else:
            database.set_working_hours(1, weekday, None, None, 0, master_id=olia)

    return {"olia": olia, "maryna": maryna}


def hours(rows):
    return {row["weekday"]: (row["start_time"], row["end_time"], row["is_open"]) for row in rows}


# ---------- which schedule applies ----------

def test_service_uses_its_masters_schedule(salon):
    assert hours(database.get_working_hours(1, 3))[0] == ("10:00", "14:00", 1)
    assert hours(database.get_working_hours(1, 3))[3] == (None, None, 0)


def test_master_without_schedule_falls_back_to_business(salon):
    assert hours(database.get_working_hours(1, 5))[0] == ("09:00", "18:00", 1)


def test_service_own_schedule_beats_the_masters(salon):
    database.set_working_hours(1, 0, "15:00", "17:00", 1, service_id=4)
    assert hours(database.get_working_hours(1, 4)) == {0: ("15:00", "17:00", 1)}
    assert hours(database.get_working_hours(1, 3))[0] == ("10:00", "14:00", 1)


def test_business_default_is_unaffected_by_master_rows(salon):
    assert hours(database.get_working_hours(1))[0] == ("09:00", "18:00", 1)
    assert hours(database.get_working_hours(1))[3] == ("09:00", "18:00", 1)


def test_saving_a_master_day_replaces_only_that_masters_day(salon):
    database.set_working_hours(1, 0, "11:00", "13:00", 1, master_id=salon["olia"])

    assert hours(database.get_master_working_hours(1, salon["olia"]))[0] == ("11:00", "13:00", 1)
    assert hours(database.get_working_hours(1))[0] == ("09:00", "18:00", 1)


def test_deleting_a_master_removes_their_schedule(salon):
    conn = database.get_connection()
    conn.execute("DELETE FROM masters WHERE id = ?", (salon["olia"],))
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM working_hours WHERE master_id IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    assert count == 0
    # Service 3 lost its master, so it's back on the business schedule.
    assert hours(database.get_working_hours(1, 3))[3] == ("09:00", "18:00", 1)


# ---------- booking respects it ----------

def test_available_times_follow_the_masters_hours(salon, frozen_now):
    assert bookings.get_available_times(1, MONDAY, 3) == ["10:00", "11:00", "12:00", "13:00"]
    assert bookings.get_available_times(1, THURSDAY, 3) is None
    assert "17:00" in bookings.get_available_times(1, MONDAY, 5)


def test_booking_time_error_uses_the_masters_hours(salon, frozen_now):
    assert "10:00 до 14:00" in bookings.booking_time_error(1, MONDAY, "15:00", 3)
    assert "не працюємо" in bookings.booking_time_error(1, THURSDAY, "11:00", 3)
    assert bookings.booking_time_error(1, THURSDAY, "11:00", 5) is None


def test_ai_prompt_shows_master_and_hours(salon):
    prompt = business_context.build_business_prompt(1)
    assert "Майстер: Оля" in prompt
    assert "графік майстра Оля" in prompt
    assert "Майстер: Марина" in prompt


# ---------- database guards ----------

def test_row_cannot_be_both_service_and_master(salon, db):
    conn = db.get_connection()
    with pytest.raises(sqlite3.IntegrityError, match="not both"):
        conn.execute(
            "INSERT INTO working_hours (business_id, weekday, is_open, service_id, master_id) "
            "VALUES (1, 6, 1, 3, ?)",
            (salon["olia"],)
        )
    conn.close()


def test_master_schedule_of_another_business_is_rejected(salon, db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO businesses (id, owner_telegram_id, name, slug) VALUES (2, 200, 'B', 'b')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="another business"):
        database.set_working_hours(2, 0, "09:00", "10:00", 1, master_id=salon["olia"])


# ---------- /schedule dialog ----------

class Message:
    def __init__(self, text=None):
        self.text = text
        self.replies = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append(types.SimpleNamespace(text=text, markup=reply_markup))


def text_update(text):
    return types.SimpleNamespace(
        message=Message(text),
        effective_user=types.SimpleNamespace(id=100, full_name="Власник"),
    )


def callback_update(data):
    async def answer(*args, **kwargs):
        pass

    message = Message()
    return types.SimpleNamespace(
        callback_query=types.SimpleNamespace(data=data, answer=answer, message=message),
        effective_user=types.SimpleNamespace(id=100, full_name="Власник"),
    )


def button_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def quiet_events(monkeypatch):
    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(schedule, "on_first_schedule", noop)


def test_master_option_is_hidden_without_masters(business):
    update = text_update("/schedule")
    run(schedule.schedule_start(update, types.SimpleNamespace(user_data={})))
    assert "schedule_scope_master" not in button_data(update.message.replies[-1].markup)


def test_owner_sets_a_masters_schedule(salon, quiet_events):
    ctx = types.SimpleNamespace(user_data={}, bot=None)

    start = text_update("/schedule")
    assert run(schedule.schedule_start(start, ctx)) == schedule.CHOOSE_SCOPE
    assert "schedule_scope_master" in button_data(start.message.replies[-1].markup)

    scope = callback_update("schedule_scope_master")
    assert run(schedule.schedule_choose_scope(scope, ctx)) == schedule.CHOOSE_MASTER
    assert f"schedule_master_{salon['maryna']}" in button_data(
        scope.callback_query.message.replies[-1].markup
    )

    pick = callback_update(f"schedule_master_{salon['maryna']}")
    assert run(schedule.schedule_choose_master(pick, ctx)) == schedule.WAITING_SCHEDULE
    assert "Марина" in pick.callback_query.message.replies[-1].text

    save = text_update("Пн 12:00-20:00\nНд вихідний")
    assert run(schedule.save_schedule(save, ctx)) == ConversationHandler.END
    assert "Графік майстра «Марина» збережено" in save.message.replies[-1].text

    assert hours(database.get_working_hours(1, 5))[0] == ("12:00", "20:00", 1)
    assert hours(database.get_working_hours(1))[0] == ("09:00", "18:00", 1)
    assert not any(key.startswith("schedule_") for key in ctx.user_data)


def test_forged_master_button_is_rejected(salon, db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO businesses (id, owner_telegram_id, name, slug) VALUES (2, 200, 'B', 'b')"
    )
    conn.commit()
    conn.close()
    stranger = masters.get_or_create_master(2, "Чужий")["id"]

    ctx = types.SimpleNamespace(user_data={"schedule_business_id": 1}, bot=None)
    update = callback_update(f"schedule_master_{stranger}")

    assert run(schedule.schedule_choose_master(update, ctx)) == ConversationHandler.END
    assert "schedule_master_id" not in ctx.user_data


def test_schedule_callback_patterns_match_buttons(salon):
    import bot  # noqa: F401  (conversation_utils needs bot's menu texts)

    handler = schedule.get_schedule_handler()
    patterns = [
        h.pattern.pattern if hasattr(h.pattern, "pattern") else h.pattern
        for state in handler.states.values() for h in state
        if getattr(h, "pattern", None) is not None
    ]
    import re
    for data in ("schedule_scope_master", f"schedule_master_{salon['olia']}"):
        assert any(re.match(p, data) for p in patterns), data
