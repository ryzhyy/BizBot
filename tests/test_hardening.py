"""Small hardening fixes: /setup slug race, working hours on button
bookings, and length limits on owner-entered text."""

import asyncio
import types

import pytest

from conftest import MONDAY

import bookings
import contact_settings
import database
import input_limits
import location_settings
import services
import setup

OWNER_ID = 100  # owner of business 1 in conftest


def run(coroutine):
    return asyncio.run(coroutine)


class Message:
    def __init__(self, text=None, contact=None):
        self.text = text
        self.contact = contact
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def text_update(text, user_id=OWNER_ID):
    return types.SimpleNamespace(
        message=Message(text),
        effective_user=types.SimpleNamespace(id=user_id, full_name="Хтось"),
        effective_chat=types.SimpleNamespace(id=user_id),
    )


def context(**user_data):
    return types.SimpleNamespace(user_data=dict(user_data), bot=None)


def business_count():
    conn = database.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    conn.close()
    return count


# ---------- /setup slug race ----------

def test_slug_collision_retries_instead_of_blaming_the_owner(business, monkeypatch):
    real = setup.generate_unique_slug
    picks = iter(["salon"])  # business 1's slug: a concurrent winner

    def racing_slug(cursor, name):
        return next(picks, None) or real(cursor, name)

    monkeypatch.setattr(setup, "generate_unique_slug", racing_slug)

    business_id = setup.create_business(555, "Салон", "Краса", "Київ")

    assert business_id is not None
    conn = database.get_connection()
    slug = conn.execute(
        "SELECT slug FROM businesses WHERE id = ?", (business_id,)
    ).fetchone()[0]
    conn.close()
    assert slug != "salon"


def test_second_business_for_the_same_owner_is_refused(business):
    assert setup.create_business(OWNER_ID, "Ще один", "Краса", "Київ") is None
    assert business_count() == 1


# ---------- button booking checks working hours ----------

def press_confirm(user_data):
    import bot

    async def answer(*args, **kwargs):
        pass

    message = Message()
    user = types.SimpleNamespace(id=777, full_name="Клієнт")
    query = types.SimpleNamespace(
        data="confirm", answer=answer, message=message, from_user=user
    )
    update = types.SimpleNamespace(
        callback_query=query, effective_user=user,
        effective_chat=types.SimpleNamespace(id=777),
    )
    run(bot.button_handler(update, context_with(user_data)))
    return message.replies


def context_with(user_data):
    return types.SimpleNamespace(user_data=user_data, bot=None)


@pytest.mark.parametrize("time, expected", [
    ("23:00", "09:00 до 18:00"),     # forged time outside working hours
    ("17:30", "09:00 до 18:00"),     # 60-min service would end after closing
])
def test_forged_time_button_is_rejected(business, frozen_now, time, expected):
    replies = press_confirm({
        "client_business_id": 1, "booking_business_id": 1,
        "service_id": 2, "service_name": "Стрижка",
        "date": MONDAY, "time": time,
    })

    assert expected in replies[-1]
    assert bookings.get_business_bookings(1) == []


def test_closed_day_button_is_rejected(business, frozen_now):
    replies = press_confirm({
        "client_business_id": 1, "booking_business_id": 1,
        "service_id": 2, "service_name": "Стрижка",
        "date": "2026-10-04", "time": "10:00",   # Sunday, closed
    })
    assert "не працюємо" in replies[-1]


def test_booking_that_became_past_while_sharing_phone_is_rejected(
    business, monkeypatch
):
    import bot
    from datetime import datetime

    monkeypatch.setattr(bookings, "now_local", lambda: datetime(2026, 10, 5, 12, 0))
    contact = types.SimpleNamespace(user_id=777, phone_number="+380501112233")
    update = types.SimpleNamespace(
        message=Message(contact=contact),
        effective_user=types.SimpleNamespace(id=777, full_name="Клієнт"),
        effective_chat=types.SimpleNamespace(id=777),
    )
    ctx = context_with({"pending_phone_booking": {
        "business_id": 1, "service_id": 2, "service_name": "Стрижка",
        "date": MONDAY, "time": "10:00",
    }})

    run(bot.handle_contact(update, ctx))

    assert "минув" in update.message.replies[-1]
    assert bookings.get_business_bookings(1) == []
    assert "pending_phone_booking" not in ctx.user_data


# ---------- text length limits ----------

LONG = "я" * 5000


@pytest.mark.parametrize("handler, state, limit", [
    (setup.setup_name, setup.NAME, input_limits.BUSINESS_NAME),
    (setup.setup_category, setup.CATEGORY, input_limits.BUSINESS_CATEGORY),
])
def test_setup_rejects_overlong_answers(handler, state, limit):
    update = text_update(LONG)
    assert run(handler(update, context())) == state
    assert str(limit) in update.message.replies[-1]


def test_setup_city_rejects_overlong_city(business):
    update = text_update(LONG)
    ctx = context(setup_name="Салон", setup_category="Краса")
    assert run(setup.setup_city(update, ctx)) == setup.CITY
    assert business_count() == 1


def test_setup_accepts_normal_answers():
    ctx = context()
    assert run(setup.setup_name(text_update("  Барбершоп  "), ctx)) == setup.CATEGORY
    assert ctx.user_data["setup_name"] == "Барбершоп"


@pytest.mark.parametrize("handler, text, state, fragment", [
    (services.service_name, LONG, services.SERVICE_NAME, "60"),
    (services.service_price, "5000000", services.SERVICE_PRICE, "1000000"),
    (services.service_duration, "100000", services.SERVICE_DURATION, "720"),
])
def test_service_flow_rejects_out_of_range_values(business, handler, text, state, fragment):
    update = text_update(text)
    ctx = context(service_business_id=1, service_name="x", service_price=1)
    assert run(handler(update, ctx)) == state
    assert fragment in update.message.replies[-1]


def test_contact_value_is_capped(business):
    update = text_update(LONG)
    assert run(contact_settings.setcontact_manual_value(update, context())) == \
        contact_settings.CONTACT_MANUAL_VALUE

    conn = database.get_connection()
    value = conn.execute(
        "SELECT support_contact_value FROM businesses WHERE id = 1"
    ).fetchone()[0]
    conn.close()
    assert value is None


def test_faq_question_and_answer_are_capped(business):
    ctx = context(faq_business_id=1)

    assert run(contact_settings.setfaq_add_question(text_update(LONG), ctx)) == \
        contact_settings.FAQ_ADD_QUESTION
    assert "faq_pending_question" not in ctx.user_data

    run(contact_settings.setfaq_add_question(text_update("Чи є парковка?"), ctx))
    assert run(contact_settings.setfaq_add_answer(text_update(LONG), ctx)) == \
        contact_settings.FAQ_ADD_ANSWER
    assert ctx.user_data["faq_pending_question"] == "Чи є парковка?"

    from business_context import get_faq_items
    assert get_faq_items(1) == []


def test_address_is_capped(business):
    update = text_update(LONG)
    assert run(location_settings.setlocation_save_address(update, context())) == \
        location_settings.LOCATION_WAITING_ADDRESS

    conn = database.get_connection()
    address = conn.execute(
        "SELECT address_text FROM businesses WHERE id = 1"
    ).fetchone()[0]
    conn.close()
    assert address is None


@pytest.mark.parametrize("raw, limit, expected", [
    ("  ок  ", 5, "ок"),
    ("", 5, None),
    ("   ", 5, None),
    ("123456", 5, None),
    (None, 5, None),
])
def test_clean_text(raw, limit, expected):
    assert input_limits.clean_text(raw, limit) == expected

