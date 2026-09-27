"""Regression tests for the P0/P1 items of the 1000-business audit."""

import asyncio
import json
import os
import types
from datetime import datetime, timedelta

import pytest
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut
from telegram.ext import CommandHandler, PicklePersistence

import ai_manager
import bookings
import database
import platform_admin
import rate_limit


def run(coroutine):
    return asyncio.run(coroutine)


# ---------- P0: debug command ----------

def test_debug_command_is_gone():
    import bot

    app = bot.build_application()
    commands = {
        command
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }

    assert "debug_client_help" not in commands
    assert not hasattr(bot, "debug_client_help")
    assert {"start", "admin", "platform"} <= commands


# ---------- P1: state survives restarts ----------

def test_application_persists_user_data_next_to_the_database():
    import bot

    persistence = bot.build_application().persistence

    assert isinstance(persistence, PicklePersistence)
    assert persistence.store_data.user_data
    assert not persistence.store_data.bot_data
    assert os.path.dirname(str(persistence.filepath)) == os.path.dirname(
        os.path.abspath(database.DB_NAME)
    )


def test_client_business_survives_a_restart(tmp_path):
    import bot

    path = tmp_path / "state.pickle"
    os.environ["STATE_PATH"] = str(path)
    try:
        async def write():
            persistence = bot.build_persistence()
            await persistence.update_user_data(777, {"client_business_id": 5})
            await persistence.flush()

        async def read():
            return await bot.build_persistence().get_user_data()

        run(write())
        assert run(read())[777] == {"client_business_id": 5}
    finally:
        del os.environ["STATE_PATH"]


def test_jobs_are_scheduled():
    import bot

    app = bot.build_application()
    names = {job.callback.__name__ for job in app.job_queue.jobs()}
    assert {"send_reminders", "run_pro_expiration_check", "send_daily_backup"} <= names


# ---------- P1: OpenAI robustness ----------

class FakeResponses:
    def __init__(self, output):
        self.output = output
        self.prompts = []

    async def create(self, model, input, **kwargs):
        self.prompts.append(input)
        return types.SimpleNamespace(output_text=self.output)


def manager_returning(output):
    manager = ai_manager.AIManager("test-key")
    manager.client = types.SimpleNamespace(responses=FakeResponses(output))
    return manager


def test_openai_client_has_a_short_timeout():
    manager = ai_manager.AIManager("test-key")
    assert manager.client.timeout == ai_manager.OPENAI_TIMEOUT_SECONDS <= 60
    assert manager.client.max_retries == ai_manager.OPENAI_MAX_RETRIES

    import bot
    assert bot.client.timeout == ai_manager.OPENAI_TIMEOUT_SECONDS


@pytest.mark.parametrize("output", [
    "not json at all",
    "[1, 2, 3]",
    '"just a string"',
    "```json\n[]\n```",
])
def test_non_object_ai_output_falls_back_to_general(db, output):
    result = run(manager_returning(output).understand_message("привіт"))
    assert result["intent"] == "general"


def test_valid_ai_output_is_returned(db):
    payload = {"intent": "booking", "service": 3, "date": "2026-10-05"}
    result = run(manager_returning(
        "```json\n" + json.dumps(payload) + "\n```"
    ).understand_message("хочу стрижку"))
    assert result == payload


def test_user_text_is_capped_before_reaching_the_model(db):
    manager = manager_returning('{"intent": "general"}')
    run(manager.understand_message("а" * 50_000))

    prompt = manager.client.responses.prompts[0]
    assert "а" * ai_manager.MAX_USER_TEXT_CHARS in prompt
    assert "а" * (ai_manager.MAX_USER_TEXT_CHARS + 1) not in prompt


# ---------- P1: per-user AI rate limit ----------

def test_rate_limiter_blocks_a_flood_and_recovers():
    now = [0.0]
    limiter = rate_limit.SlidingWindowLimiter(3, 60, clock=lambda: now[0])

    assert [limiter.allow("u") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("someone else")

    now[0] = 61
    assert limiter.allow("u")


def test_rate_limiter_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_TRACKED_USERS", 100)
    now = [0.0]
    limiter = rate_limit.SlidingWindowLimiter(3, 60, clock=lambda: now[0])

    for user in range(100):
        limiter.allow(user)
    now[0] = 120
    limiter.allow("new")

    assert len(limiter._hits) == 1


def test_flooding_user_gets_throttled_without_calling_openai(monkeypatch, db):
    import bot

    calls = []

    async def understand(*args, **kwargs):
        calls.append(1)
        return {"intent": "unknown"}

    monkeypatch.setattr(bot.ai_manager, "understand_message", understand)
    monkeypatch.setattr(
        bot, "ai_limiter", rate_limit.SlidingWindowLimiter(2, 60)
    )

    async def respond(*args, **kwargs):
        return types.SimpleNamespace(output_text="ok")

    monkeypatch.setattr(bot.client.responses, "create", respond)

    replies = []

    async def reply_text(text, **kwargs):
        replies.append(text)

    def message():
        return types.SimpleNamespace(
            message=types.SimpleNamespace(text="привіт", reply_text=reply_text),
            effective_user=types.SimpleNamespace(id=5),
        )

    context = types.SimpleNamespace(user_data={}, bot=None)
    for _ in range(3):
        run(bot.ai_message(message(), context))

    assert len(calls) == 2
    assert "Забагато повідомлень" in replies[-1]


# ---------- P1: reminders ----------

@pytest.fixture
def upcoming(business, monkeypatch):
    """A Pro business and a frozen clock at Sunday 22:30, so the 2-hour
    reminder window crosses midnight. Bookings: #1 in 1h (Sunday),
    #3 in 1h45m (00:15 Monday), #2 in 3h (outside the window)."""
    now = datetime(2026, 10, 4, 22, 30)
    monkeypatch.setattr(bookings, "now_local", lambda: now)

    conn = database.get_connection()
    conn.execute("INSERT INTO customers (id, business_id, telegram_id) VALUES (1, 1, 501)")
    for booking_id, when in (
        (1, now + timedelta(hours=1)),
        (2, now + timedelta(hours=3)),
        (3, now + timedelta(hours=1, minutes=45)),
    ):
        conn.execute(
            "INSERT INTO bookings (id, business_id, customer_id, service_id, "
            "booking_date, booking_time) VALUES (?, 1, 1, 2, ?, ?)",
            (booking_id, when.strftime("%Y-%m-%d"), when.strftime("%H:%M"))
        )
    conn.commit()
    conn.close()
    return now


def test_reminder_query_finds_only_the_window_across_midnight(upcoming):
    rows = bookings.get_upcoming_bookings_needing_reminder(hours_ahead=2)
    assert [(row["id"], row["date"], row["time"]) for row in rows] == [
        (1, "2026-10-04", "23:30"),
        (3, "2026-10-05", "00:15"),
    ]


def test_reminder_query_uses_the_partial_index(upcoming):
    conn = database.get_connection()
    plan = " ".join(
        row[-1] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM bookings "
            "WHERE status = 'confirmed' AND reminder_sent = 0 "
            "AND booking_date BETWEEN '2026-10-04' AND '2026-10-05'"
        )
    )
    conn.close()
    assert "idx_bookings_pending_reminder" in plan


class ReminderBot:
    def __init__(self, *errors):
        self.errors = list(errors)
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(chat_id)


def reminder_sent_flag(booking_id):
    conn = database.get_connection()
    flag = conn.execute(
        "SELECT reminder_sent FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()[0]
    conn.close()
    return flag


@pytest.fixture
def fast_reminders(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "REMINDER_SEND_INTERVAL_SECONDS", 0)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    async def no_report(*args, **kwargs):
        pass

    monkeypatch.setattr(bot, "report_error", no_report)
    return bot, sleeps


@pytest.mark.parametrize("errors, sent, marked", [
    ((), [501], 1),
    ((RetryAfter(3),), [501], 1),               # waits, then retries
    ((RetryAfter(3), RetryAfter(3)), [], 0),    # left for the next run
    ((TimedOut(),), [], 0),                     # transient: retry later
    ((Forbidden("blocked"),), [], 1),           # client blocked the bot
    ((BadRequest("Chat not found"),), [], 1),   # permanent
])
def test_reminder_delivery_outcomes(upcoming, fast_reminders, errors, sent, marked):
    bot, sleeps = fast_reminders
    # Only booking #1's delivery fails as scripted; #3 then goes through.
    fake = ReminderBot(*errors)

    run(bot.send_reminders(types.SimpleNamespace(bot=fake)))

    assert fake.sent == sent + [501]
    assert reminder_sent_flag(1) == marked
    assert reminder_sent_flag(3) == 1
    assert reminder_sent_flag(2) == 0
    if errors[:1] and isinstance(errors[0], RetryAfter):
        assert 3.0 in sleeps


def test_no_reminders_for_free_businesses(upcoming, fast_reminders, db):
    bot, _ = fast_reminders
    conn = db.get_connection()
    conn.execute("UPDATE businesses SET pro_until = NULL")
    conn.commit()
    conn.close()

    fake = ReminderBot()
    run(bot.send_reminders(types.SimpleNamespace(bot=fake)))
    assert fake.sent == []


# ---------- P1: /platform with 1000 businesses ----------

def test_platform_list_is_paged_for_a_thousand_businesses(db):
    conn = db.get_connection()
    conn.executemany(
        "INSERT INTO businesses (owner_telegram_id, name, category, slug) "
        "VALUES (?, ?, 'Салон', ?)",
        [(10_000 + i, f"Бізнес {i}", f"biz-{i}") for i in range(1000)]
    )
    conn.commit()
    conn.close()

    seen = set()
    page, pages = 0, None
    while True:
        text, keyboard = platform_admin._business_list_message(page)
        buttons = [b for row in keyboard.inline_keyboard for b in row]

        assert len(buttons) <= 25 and len(text) < 4096
        assert "Усього бізнесів: 1000" in text
        seen |= {b.callback_data for b in buttons if b.callback_data.startswith("platform_biz_")}

        forward = [b for b in buttons if b.text == "▶️"]
        if not forward:
            break
        page = int(forward[0].callback_data.removeprefix("platform_page_"))

    assert len(seen) == 1000


def test_platform_page_handler_is_owner_only(monkeypatch, db):
    monkeypatch.setattr(platform_admin, "OWNER_TELEGRAM_ID", "1")
    replies, edits = [], []

    async def answer(*args, **kwargs):
        pass

    async def reply_text(text, **kwargs):
        replies.append(text)

    async def edit_message_text(text, **kwargs):
        edits.append(text)

    query = types.SimpleNamespace(
        data="platform_page_3", answer=answer,
        from_user=types.SimpleNamespace(id=2),
        message=types.SimpleNamespace(reply_text=reply_text),
        edit_message_text=edit_message_text,
    )
    run(platform_admin.platform_page(types.SimpleNamespace(callback_query=query), None))

    assert edits == [] and "лише власнику платформи" in replies[0]

