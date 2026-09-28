"""Демо-салон Beauty Studio: повний шлях клієнта через обробники бота.

Салон створюється так само, як у проді (demo_data.seed_business), а
клієнт проходить його кнопками: посилання → послуги → послуга →
майстер → дата → вільний час → підтвердження → телефон → «Мої записи»
→ перенесення → скасування.
"""

import asyncio
import types

import pytest

from conftest import FROZEN_NOW, MONDAY

import bookings
import database
import demo_data
import masters
import platform_admin
import rate_limit

OWNER = 500
CLIENT = 777
OTHER_CLIENT = 778
TUESDAY = "2026-10-06"
SATURDAY = "2026-10-10"


class Message:
    def __init__(self, text=None, contact=None):
        self.text = text
        self.contact = contact
        self.replies = []
        self.markups = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append(text)
        self.markups.append(reply_markup)


class Bot:
    username = "BizBotTest"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def buttons(markup):
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard for button in row
    ]


class Client:
    """Один клієнт Telegram, що натискає кнопки й пише боту."""

    def __init__(self, bot_module, telegram_id=CLIENT):
        self.bot = bot_module
        self.user = types.SimpleNamespace(id=telegram_id, full_name="Клієнтка")
        self.chat = types.SimpleNamespace(id=telegram_id)
        self.context = types.SimpleNamespace(user_data={}, bot=Bot(), args=[])

    def _update(self, **fields):
        return types.SimpleNamespace(
            effective_user=self.user, effective_chat=self.chat, **fields
        )

    def open_link(self, slug):
        message = Message()
        self.context.args = [slug]
        asyncio.run(self.bot.start(self._update(message=message), self.context))
        return message

    def press(self, data):
        async def answer(*args, **kwargs):
            pass

        message = Message()
        query = types.SimpleNamespace(
            data=data, answer=answer, message=message, from_user=self.user
        )
        asyncio.run(self.bot.button_handler(
            self._update(callback_query=query), self.context
        ))
        return message

    def share_phone(self):
        contact = types.SimpleNamespace(
            user_id=self.user.id, phone_number="+380501112233"
        )
        message = Message(contact=contact)
        asyncio.run(self.bot.handle_contact(
            self._update(message=message), self.context
        ))
        return message

    def say(self, text):
        message = Message(text)
        asyncio.run(self.bot.ai_message(self._update(message=message), self.context))
        return message


@pytest.fixture
def salon(db, monkeypatch):
    import bot

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(bot, "on_start", noop)
    monkeypatch.setattr(bot, "on_booking_created", noop)
    monkeypatch.setattr(bot, "now_local", lambda: FROZEN_NOW)  # Нд 09:00
    monkeypatch.setattr(bookings, "now_local", lambda: FROZEN_NOW)
    monkeypatch.setattr(bot, "ai_limiter", rate_limit.SlidingWindowLimiter(100, 60))

    business_id = demo_data.seed_business(
        OWNER, demo_data.DEMO_BUSINESSES["beauty-studio"]
    )
    services = {s["name"].split(" ", 1)[1]: s for s in bot.get_visible_services(business_id)}
    return types.SimpleNamespace(bot=bot, id=business_id, services=services)


def service_id(salon, name):
    return salon.services[name]["id"]


# ---------- дані салону ----------

def test_salon_is_created_with_masters_services_and_schedules(salon):
    assert demo_data.get_business_slug(salon.id) == "beauty-studio"
    assert [m["name"] for m in masters.get_masters(salon.id)] == [
        "Анна", "Марія", "Олена", "Софія"
    ]
    assert {
        name: (s["price"], s["duration"], s["master_name"])
        for name, s in salon.services.items()
    } == {
        "Жіноча стрижка": (700, 60, "Анна"),
        "Фарбування волосся": (1500, 120, "Анна"),
        "Манікюр": (600, 60, "Марія"),
        "Оформлення брів": (450, 40, "Софія"),
        "Масаж обличчя": (800, 60, "Олена"),
    }

    # Кожна майстриня має власний графік: Пн–Пт 09–18 з обідом, Сб–Нд вихідні.
    for master in masters.get_masters(salon.id):
        rows = database.get_master_working_hours(salon.id, master["id"])
        assert [database.format_day_hours(row) for row in rows] == (
            ["09:00–18:00 (перерва 13:00–14:00)"] * 5 + ["вихідний"] * 2
        )


def test_every_master_schedule_can_be_changed_separately(salon):
    anna = masters.get_or_create_master(salon.id, "Анна")["id"]
    database.set_working_hours(salon.id, 0, "12:00", "20:00", 1, master_id=anna)

    assert bookings.get_available_times(
        salon.id, MONDAY, service_id(salon, "Жіноча стрижка")
    )[0] == "12:00"
    assert bookings.get_available_times(
        salon.id, MONDAY, service_id(salon, "Манікюр")
    )[0] == "09:00"


# ---------- повний шлях клієнта ----------

def test_full_client_journey(salon):
    client = Client(salon.bot)
    manicure = service_id(salon, "Манікюр")

    welcome = client.open_link("beauty-studio")
    assert "Beauty Studio" in welcome.replies[0]

    # Перегляд послуг: ціна й майстер, емодзі не дублюється.
    services_text = client.press("services").replies[-1]
    assert "💅 Манікюр — 600 грн\n👤 Майстер: Марія\n" in services_text
    assert "🎨 Фарбування волосся — 1500 грн\n👤 Майстер: Анна\n" in services_text
    assert "👁️ Оформлення брів — 450 грн\n👤 Майстер: Софія\n" in services_text

    # Вибір послуги.
    choice = client.press("book")
    labels = [text for text, _ in buttons(choice.markups[-1])]
    assert "💅 Манікюр — 600 грн · Марія" in labels
    assert "💇‍♀️ Жіноча стрижка — 700 грн · Анна" in labels
    assert len(labels) == 5

    # Манікюр → лише Марія.
    picked = client.press(f"service_{manicure}")
    assert "👤 Майстер: Марія" in picked.replies[-1]
    for other in ("Анна", "Софія", "Олена"):
        assert other not in picked.replies[-1]

    # Дати: лише робочі дні майстрині (з неділі — Пн, Вт, Ср, Чт).
    dates = [data for _, data in buttons(picked.markups[-1]) if data != "cancel"]
    assert dates == [
        "date_2026-10-05", "date_2026-10-06", "date_2026-10-07", "date_2026-10-08"
    ]

    # Час: без обіду 13:00.
    times = client.press(f"date_{MONDAY}")
    assert [data for _, data in buttons(times.markups[-1]) if data != "cancel"] == [
        "time_09:00", "time_10:00", "time_11:00", "time_12:00",
        "time_14:00", "time_15:00", "time_16:00", "time_17:00",
    ]

    summary = client.press("time_10:00").replies[-1]
    assert "📋 Ваш запис:" in summary
    assert "💅 Манікюр\n👤 Майстер: Марія\n📅 2026-10-05\n🕐 10:00" in summary

    # Підтвердження → бот просить телефон → запис створено.
    client.press("confirm")
    assert "номером телефону" in client.context.bot.sent[-1][1]
    client.share_phone()

    success = client.context.bot.sent[-1][1]
    assert success.startswith("✅ Запис підтверджено!")
    assert "👤 Майстер: Марія" in success

    owner_note = next(text for chat, text in client.context.bot.sent if chat == OWNER)
    assert "Марія" in owner_note

    saved = bookings.get_business_bookings(salon.id)
    assert len(saved) == 1

    # «Мої записи».
    mine = client.press("mybookings").replies[-1]
    assert "💅 Манікюр\n👤 Майстер: Марія\n📅 2026-10-05\n🕐 10:00" in mine

    # Цей час у Марії вже зайнятий, у інших майстринь — вільний.
    assert "10:00" not in bookings.get_available_times(salon.id, MONDAY, manicure)
    assert "10:00" in bookings.get_available_times(
        salon.id, MONDAY, service_id(salon, "Оформлення брів")
    )


def ai_replies(monkeypatch, bot, *results):
    queue = list(results)

    async def understand(*args, **kwargs):
        return queue.pop(0)

    monkeypatch.setattr(bot.ai_manager, "understand_message", understand)


def book(salon, client, service_name, date, time):
    client.open_link("beauty-studio")
    client.press("book")
    client.press(f"service_{service_id(salon, service_name)}")
    client.press(f"date_{date}")
    client.press(f"time_{time}")
    client.press("confirm")
    if client.context.user_data.get("pending_phone_booking"):
        client.share_phone()
    return client.context.bot.sent[-1][1]


def test_reschedule_and_cancel(salon, monkeypatch):
    client = Client(salon.bot)
    assert book(salon, client, "Масаж обличчя", MONDAY, "10:00").startswith("✅")

    # Перенесення в обід — відмова, запис лишається на місці.
    ai_replies(monkeypatch, salon.bot, {
        "intent": "reschedule_booking", "date": MONDAY, "time": "13:00"
    })
    assert "перерва" in client.say("перенеси на 13:00").replies[-1]

    ai_replies(
        monkeypatch, salon.bot,
        {"intent": "reschedule_booking", "date": MONDAY, "time": "15:00"},
        {"intent": "confirm"},
    )
    prompt = client.say("перенеси на 15:00").replies[-1]
    assert "🔄 Перенести запис?" in prompt
    assert "👤 Майстер: Олена" in prompt
    assert "перенесено" in client.say("так").replies[-1]

    [booking] = bookings.get_business_bookings(salon.id)
    assert (booking["date"], booking["time"]) == (MONDAY, "15:00")
    assert "10:00" in bookings.get_available_times(
        salon.id, MONDAY, service_id(salon, "Масаж обличчя")
    )

    # Скасування.
    ai_replies(monkeypatch, salon.bot, {"intent": "cancel_booking", "date": MONDAY})
    assert "👤 Майстер: Олена" in client.say("скасуй запис").replies[-1]
    assert "Скасовано записів: 1" in client.say("так").replies[-1]

    assert "У вас поки немає записів" in client.press("mybookings").replies[-1]
    assert "15:00" in bookings.get_available_times(
        salon.id, MONDAY, service_id(salon, "Масаж обличчя")
    )


# ---------- майстер ↔ послуга і конфлікти ----------

@pytest.mark.parametrize("service_name, master, others", [
    ("Манікюр", "Марія", ("Анна", "Софія", "Олена")),
    ("Жіноча стрижка", "Анна", ("Марія", "Софія", "Олена")),
    ("Фарбування волосся", "Анна", ("Марія", "Софія", "Олена")),
    ("Оформлення брів", "Софія", ("Анна", "Марія", "Олена")),
    ("Масаж обличчя", "Олена", ("Анна", "Марія", "Софія")),
])
def test_choosing_a_service_shows_only_its_master(salon, service_name, master, others):
    client = Client(salon.bot)
    client.open_link("beauty-studio")
    client.press("book")

    text = client.press(f"service_{service_id(salon, service_name)}").replies[-1]

    assert f"👤 Майстер: {master}" in text
    assert not any(name in text for name in others)


def test_annas_colouring_blocks_only_anna(salon):
    colouring = service_id(salon, "Фарбування волосся")
    haircut = service_id(salon, "Жіноча стрижка")

    assert book(salon, Client(salon.bot), "Фарбування волосся", MONDAY, "10:00").startswith("✅")

    # Фарбування 10:00–12:00: стрижку в Анни не можна ні о 10:00, ні об 11:00.
    haircut_times = bookings.get_available_times(salon.id, MONDAY, haircut)
    assert haircut_times == ["09:00", "12:00", "14:00", "15:00", "16:00", "17:00"]
    for time in ("10:00", "11:00", "11:30"):
        assert "зайнятий" in bookings.booking_time_error(salon.id, MONDAY, time, haircut)
    assert bookings.booking_time_error(salon.id, MONDAY, "12:00", haircut) is None

    # Друге фарбування (120 хв) не влазить ні перед обідом, ні в обід.
    assert bookings.get_available_times(salon.id, MONDAY, colouring) == [
        "14:00", "15:00", "16:00"
    ]

    # Інші майстрині о 10:00 вільні.
    for name in ("Манікюр", "Оформлення брів", "Масаж обличчя"):
        assert "10:00" in bookings.get_available_times(
            salon.id, MONDAY, service_id(salon, name)
        )

    # Навіть якщо кнопку підробити, другий клієнт в Анну на 11:00 не запишеться.
    other = Client(salon.bot, OTHER_CLIENT)
    other.open_link("beauty-studio")
    other.context.user_data.update({
        "booking_business_id": salon.id, "service_id": haircut,
        "service_name": "💇‍♀️ Жіноча стрижка", "date": MONDAY, "time": "11:00",
    })
    assert "зайнятий" in other.press("confirm").replies[-1]
    assert len(bookings.get_business_bookings(salon.id)) == 1


def test_break_and_days_off_are_enforced(salon):
    haircut = service_id(salon, "Жіноча стрижка")
    brows = service_id(salon, "Оформлення брів")

    assert "перерва" in bookings.booking_time_error(salon.id, MONDAY, "13:00", haircut)
    # 40 хв брів о 12:30 заходять в обід на 10 хв.
    assert "перерва" in bookings.booking_time_error(salon.id, MONDAY, "12:30", brows)
    assert bookings.booking_time_error(salon.id, MONDAY, "12:20", brows) is None
    assert "не працюємо" in bookings.booking_time_error(salon.id, SATURDAY, "10:00", haircut)
    assert bookings.get_available_times(salon.id, SATURDAY, haircut) is None


def test_ai_booking_into_the_break_is_refused(salon, monkeypatch):
    client = Client(salon.bot)
    client.open_link("beauty-studio")
    ai_replies(monkeypatch, salon.bot, {
        "intent": "confirm", "service": service_id(salon, "Манікюр"),
        "date": TUESDAY, "time": "13:00",
    })

    assert "перерва" in client.say("манікюр у вівторок о 13").replies[-1]
    assert bookings.get_business_bookings(salon.id) == []


def test_reminder_names_the_master(salon, monkeypatch):
    book(salon, Client(salon.bot), "Манікюр", MONDAY, "10:00")
    monkeypatch.setattr(
        bookings, "now_local",
        lambda: FROZEN_NOW.replace(day=5, hour=8, minute=30)
    )

    [reminder] = bookings.get_upcoming_bookings_needing_reminder(hours_ahead=2)
    text = salon.bot._reminder_text(reminder)

    assert "💅 Манікюр\n👤 Майстер: Марія\n" in text


# ---------- демо-дані універсальні ----------

def test_any_business_spec_can_be_seeded(db):
    spec = {
        "name": "Барбершоп Тест",
        "category": "Барбершоп",
        "city": "Львів",
        "schedule": [("10:00", "20:00", None, None)] * 6 + [None],
        "masters": [
            {"name": "Іван", "services": [("Стрижка", 400, 45)]},
            {
                "name": "Петро",
                "schedule": [None] * 5 + [("10:00", "16:00", "12:00", "12:30")] * 2,
                "services": [("Борода", 250, 30)],
            },
        ],
    }

    business_id = demo_data.seed_business(1, spec, pro_days=0)
    services = {s["name"]: s for s in database_services(business_id)}

    assert demo_data.get_business_slug(business_id) == "barbershop-test"
    assert bookings.get_available_times(business_id, MONDAY, services["Стрижка"]["id"])[0] == "10:00"
    assert bookings.get_available_times(business_id, MONDAY, services["Борода"]["id"]) is None
    assert "перерва" in bookings.booking_time_error(
        business_id, SATURDAY, "12:00", services["Борода"]["id"]
    )


def database_services(business_id):
    from business_context import get_business_services
    return get_business_services(business_id)


def test_owner_with_a_business_gets_none(db):
    spec = demo_data.DEMO_BUSINESSES["beauty-studio"]
    assert demo_data.seed_business(OWNER, spec) is not None
    assert demo_data.seed_business(OWNER, spec) is None


def test_failed_seed_leaves_nothing_behind(db):
    spec = dict(demo_data.DEMO_BUSINESSES["beauty-studio"])
    spec["masters"] = [{"name": "Анна", "services": [("Без ціни", None, 60)]}]

    with pytest.raises(Exception):
        demo_data.seed_business(OWNER, spec)

    conn = db.get_connection()
    leftovers = [
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("businesses", "masters", "services", "working_hours")
    ]
    conn.close()
    assert leftovers == [0, 0, 0, 0]


# ---------- /demo ----------

def run_demo(user_id, args):
    message = Message()
    update = types.SimpleNamespace(
        message=message, effective_user=types.SimpleNamespace(id=user_id)
    )
    context = types.SimpleNamespace(args=args, bot=Bot())
    asyncio.run(demo_data.demo_command(update, context))
    return message.replies[-1]


def test_demo_command_is_for_the_platform_owner_only(db, monkeypatch):
    monkeypatch.setattr(platform_admin, "OWNER_TELEGRAM_ID", "1")

    assert "лише власнику платформи" in run_demo(2, ["beauty-studio", "500"])
    assert "Використання" in run_demo(1, ["unknown", "500"])
    assert "Використання" in run_demo(1, ["beauty-studio"])

    reply = run_demo(1, ["beauty-studio", "500"])
    assert "Beauty Studio" in reply
    assert "https://t.me/BizBotTest?start=beauty-studio" in reply

    assert "вже є бізнес" in run_demo(1, ["beauty-studio", "500"])
