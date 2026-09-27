"""Owner-panel dialogs driven with stand-in Telegram objects: the
handlers are called the way python-telegram-bot calls them, and every
reply is recorded instead of being sent."""

import asyncio
import re
import types

import pytest
from telegram.ext import ConversationHandler

import database
import masters
import services

OWNER_ID = 100  # owner of business 1 in conftest


class Message:
    def __init__(self, text=None):
        self.text = text
        self.replies = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append(types.SimpleNamespace(text=text, markup=reply_markup))


def text_update(text):
    return types.SimpleNamespace(
        message=Message(text),
        callback_query=None,
        effective_user=types.SimpleNamespace(id=OWNER_ID, full_name="Власник"),
    )


def callback_update(data):
    async def answer(*args, **kwargs):
        pass

    message = Message()
    query = types.SimpleNamespace(
        data=data, message=message, answer=answer,
        from_user=types.SimpleNamespace(id=OWNER_ID),
    )
    return types.SimpleNamespace(
        message=None,
        callback_query=query,
        effective_user=types.SimpleNamespace(id=OWNER_ID, full_name="Власник"),
    )


def context():
    return types.SimpleNamespace(user_data={}, bot=None)


def run(coroutine):
    return asyncio.run(coroutine)


def button_data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture(autouse=True)
def quiet_owner_events(monkeypatch):
    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(services, "on_service_added", noop)


def service_row(name):
    conn = database.get_connection()
    row = conn.execute(
        "SELECT services.duration, masters.name AS master FROM services "
        "LEFT JOIN masters ON masters.id = services.master_id "
        "WHERE services.name = ?",
        (name,)
    ).fetchone()
    conn.close()
    return row


def add_service_until_master_question(ctx, name, price="500", duration="45"):
    assert run(services.addservice_start(text_update("/addservice"), ctx)) == services.SERVICE_NAME
    assert run(services.service_name(text_update(name), ctx)) == services.SERVICE_PRICE
    assert run(services.service_price(text_update(price), ctx)) == services.SERVICE_DURATION

    update = text_update(duration)
    assert run(services.service_duration(update, ctx)) == services.SERVICE_MASTER
    return update.message.replies[-1]


def test_add_service_with_a_new_master(business):
    ctx = context()
    question = add_service_until_master_question(ctx, "Педикюр")

    assert "Хто виконує" in question.text
    assert button_data(question.markup) == ["addsvc_master_new", "addsvc_master_none"]

    assert run(services.service_master_pick(
        callback_update("addsvc_master_new"), ctx
    )) == services.SERVICE_MASTER_NAME

    update = text_update("  Оля ")
    assert run(services.service_master_name(update, ctx)) == ConversationHandler.END
    assert "👤 Оля" in update.message.replies[-1].text

    assert tuple(service_row("Педикюр")) == (45, "Оля")
    assert ctx.user_data == {}


def test_add_service_picking_an_existing_master_or_none(business):
    olia = masters.get_or_create_master(1, "Оля")["id"]

    ctx = context()
    question = add_service_until_master_question(ctx, "Брови")
    assert f"addsvc_master_{olia}" in button_data(question.markup)
    run(services.service_master_pick(callback_update(f"addsvc_master_{olia}"), ctx))
    assert service_row("Брови")["master"] == "Оля"

    ctx = context()
    add_service_until_master_question(ctx, "Вії")
    run(services.service_master_pick(callback_update("addsvc_master_none"), ctx))
    assert service_row("Вії")["master"] is None


def test_add_service_rejects_a_forged_master_id(business):
    ctx = context()
    add_service_until_master_question(ctx, "Масаж")

    update = callback_update("addsvc_master_999")
    assert run(services.service_master_pick(update, ctx)) == services.SERVICE_MASTER
    assert service_row("Масаж") is None


def test_change_master_from_services_list(business):
    list_update = text_update("📋 Мої послуги")
    run(services.services_list(list_update, context()))
    reply = list_update.message.replies[-1]
    assert "👤 без майстра" in reply.text
    assert "svcmaster_1" in button_data(reply.markup)

    ctx = context()
    start = callback_update("svcmaster_1")
    assert run(masters.assign_start(start, ctx)) == masters.PICK_MASTER
    assert "setmaster_new" in button_data(start.callback_query.message.replies[-1].markup)

    assert run(masters.assign_pick(callback_update("setmaster_new"), ctx)) == masters.NEW_MASTER_NAME
    done = text_update("Марина")
    assert run(masters.assign_new_name(done, ctx)) == ConversationHandler.END
    assert "Марина" in done.message.replies[-1].text
    assert masters.get_service_master_name(1) == "Марина"

    ctx = context()
    run(masters.assign_start(callback_update("svcmaster_1"), ctx))
    run(masters.assign_pick(callback_update("setmaster_none"), ctx))
    assert masters.get_service_master_name(1) is None


def test_change_master_refuses_someone_elses_service(business, db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO businesses (id, owner_telegram_id, name, slug) "
        "VALUES (2, 200, 'Інший', 'other')"
    )
    conn.execute(
        "INSERT INTO services (id, business_id, name, price, duration) "
        "VALUES (9, 2, 'Чужа', 100, 30)"
    )
    conn.commit()
    conn.close()

    update = callback_update("svcmaster_9")
    assert run(masters.assign_start(update, context())) == ConversationHandler.END
    assert "не знайдено" in update.callback_query.message.replies[-1].text


def test_callback_patterns_match_the_buttons_we_send(business):
    import bot  # noqa: F401  (conversation_utils needs bot's menu texts)

    masters.get_or_create_master(1, "Оля")
    handlers = [services.get_addservice_handler(), masters.get_assign_master_handler()]

    patterns = [
        handler.pattern
        for conversation in handlers
        for state_handlers in list(conversation.states.values()) + [conversation.entry_points]
        for handler in state_handlers
        if getattr(handler, "pattern", None) is not None
    ]

    sent = (
        button_data(masters.master_picker_keyboard(1, "addsvc_master_"))
        + button_data(masters.master_picker_keyboard(1, "setmaster_"))
        + ["svcmaster_1"]
    )
    for data in sent:
        assert any(re.match(pattern, data) for pattern in patterns), data
