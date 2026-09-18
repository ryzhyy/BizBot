import os
import sqlite3
from datetime import datetime, timedelta

from setup import get_setup_handler
from schedule import get_schedule_handler
from database import init_database, get_working_hours

from business_context import (
    build_business_prompt,
    get_business_by_owner,
    get_business_by_id,
)
from services import (
    get_addservice_handler,
    get_services_handler,
)
from ai_manager import AIManager
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ADMIN_ID = 635400979

init_database()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)
ai_manager = AIManager(OPENAI_API_KEY)

if not TOKEN:
    raise ValueError("Не знайдено TELEGRAM_BOT_TOKEN у .env")

if not OPENAI_API_KEY:
    raise ValueError("Не знайдено OPENAI_API_KEY у .env")


# =========================
# DATABASE
# =========================

def get_connection():
    return sqlite3.connect("bizbot.db")


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            client_name TEXT,
            service TEXT,
            date TEXT,
            time TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_booking(telegram_id, client_name, service, date, time):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO bookings
        (telegram_id, client_name, service, date, time, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        telegram_id,
        client_name,
        service,
        date,
        time,
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def slot_is_taken(date, time):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM bookings WHERE date = ? AND time = ?",
        (date, time)
    )

    booking = cursor.fetchone()
    conn.close()

    return booking is not None


def get_user_bookings(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT id, service, date, time
        FROM bookings
        WHERE telegram_id = ?
        AND date >= ?
        ORDER BY date, time
    """, (telegram_id, today))

    bookings = cursor.fetchall()
    conn.close()

    return bookings

def delete_booking(booking_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM bookings WHERE id = ?",
        (booking_id,)
    )

    conn.commit()
    conn.close()
def reschedule_booking(booking_id, new_date, new_time):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE bookings
        SET date = ?, time = ?
        WHERE id = ?
        """,
        (new_date, new_time, booking_id)
    )

    conn.commit()
    conn.close()

# =========================
# MAIN MENU
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # Перевіряємо deep-link:
    # /start business_1
    if context.args:
        start_parameter = context.args[0]

        if start_parameter.startswith("business_"):
            try:
                business_id = int(
                    start_parameter.replace("business_", "")
                )

                business = get_business_by_id(business_id)

                if not business:
                    await update.message.reply_text(
                        "⚠️ Цей бізнес не знайдено."
                    )
                    return

                # Запам'ятовуємо, до якого бізнесу
                # підключений цей клієнт
                context.user_data["client_business_id"] = business_id

                keyboard = [
                    [
                        InlineKeyboardButton(
                            "✂️ Записатися",
                            callback_data="book"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🧾 Послуги та ціни",
                            callback_data="services"
                        ),
                        InlineKeyboardButton(
                            "📅 Мої записи",
                            callback_data="mybookings"
                        )
                    ]
                ]

                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"👋 Вітаємо у «{business['name']}»!\n\n"
                    f"✨ Я ваш персональний AI-асистент.\n\n"
                    f"Допоможу обрати послугу, дізнатися ціну "
                    f"та знайти зручний час для запису.\n\n"
                    f"Оберіть дію нижче 👇\n\n"
                    f"💬 Або просто напишіть мені, наприклад:\n"
                    f"«Хочу стрижку завтра о 17:00»",
                    reply_markup=reply_markup
                )
                return

            except (ValueError, TypeError):
                await update.message.reply_text(
                    "⚠️ Некоректне посилання на бізнес."
                )
                return

    # Звичайний /start без business ID
    await update.message.reply_text(
        "👋 Вітаю у BizBot!\n\n"
        "Якщо ви власник бізнесу — використайте /setup.\n\n"
        "Якщо ви клієнт — відкрийте персональне "
        "посилання потрібного бізнесу."
    )

# =========================
# MY BOOKINGS
# =========================

async def my_bookings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    bookings = get_user_bookings(
        update.effective_user.id
    )

    if not bookings:
        await update.message.reply_text(
            "📭 У вас поки немає записів."
        )
        return

    text = "📋 Ваші записи:\n\n"

    for booking in bookings:
        booking_id, service, date, time = booking

        text += (
            f"#{booking_id}\n"
            f"✂️ {service}\n"
            f"📅 {date}\n"
            f"🕐 {time}\n\n"
        )

    await update.message.reply_text(text)


# =========================
# ADMIN
# =========================

async def admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ У вас немає доступу."
        )
        return

    bookings = get_all_bookings()

    if not bookings:
        await update.message.reply_text(
            "📭 Записів поки немає."
        )
        return

    await update.message.reply_text(
        "👑 ПАНЕЛЬ ВЛАСНИКА\n\n"
        f"Усього записів: {len(bookings)}"
    )

    for booking in bookings:

        booking_id, client_name, service, date, time = booking

        keyboard = [[
            InlineKeyboardButton(
                "❌ Скасувати запис",
                callback_data=f"admin_delete_{booking_id}"
            )
        ]]

        await update.message.reply_text(
            f"🆔 Запис #{booking_id}\n\n"
            f"👤 {client_name}\n"
            f"✂️ {service}\n"
            f"📅 {date}\n"
            f"🕐 {time}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# =========================
# BUTTON HANDLER
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    data = query.data

    # SERVICES

    if data == "services":

        await query.message.reply_text(
            "✂️ Наші послуги:\n\n"
            "Стрижка — 500 грн\n"
            "Борода — 300 грн\n"
            "Стрижка + борода — 700 грн"
        )

    # CONTACTS

    elif data == "contacts":

        await query.message.reply_text(
            "📍 Barber Demo\n\n"
            "Львів\n"
            "Пн–Сб: 10:00–20:00\n\n"
            "☎️ +380 XX XXX XX XX"
        )

    # MY BOOKINGS BUTTON

    elif data == "mybookings":

        bookings = get_user_bookings(
            query.from_user.id
        )

        if not bookings:

            await query.message.reply_text(
                "📭 У вас поки немає записів."
            )
            return

        text = "📋 Ваші записи:\n\n"

        for booking in bookings:

            booking_id, service, date, time = booking

            text += (
                f"#{booking_id}\n"
                f"✂️ {service}\n"
                f"📅 {date}\n"
                f"🕐 {time}\n\n"
            )

        await query.message.reply_text(text)

    # BOOK

    elif data == "book":

        keyboard = [
            [InlineKeyboardButton(
                "✂️ Стрижка — 500 грн",
                callback_data="service_haircut"
            )],
            [InlineKeyboardButton(
                "🧔 Борода — 300 грн",
                callback_data="service_beard"
            )],
            [InlineKeyboardButton(
                "✂️ Стрижка + борода — 700 грн",
                callback_data="service_combo"
            )]
        ]

        await query.message.reply_text(
            "Оберіть послугу:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # SERVICE

    elif data.startswith("service_"):

        services = {
            "service_haircut": "Стрижка",
            "service_beard": "Борода",
            "service_combo": "Стрижка + борода"
        }

        context.user_data["service"] = services[data]
        
        service_codes = {
        "service_haircut": "haircut",
        "service_beard": "beard",
        "service_combo": "combo"
        }
        
        ai_state = context.user_data.get("ai_state", {})
        ai_state["service"] = service_codes[data]
        context.user_data["ai_state"] = ai_state
        today = datetime.now()

        weekdays = [
            "Пн", "Вт", "Ср", "Чт",
            "Пт", "Сб", "Нд"
        ]

        months = [
            "",
            "січня", "лютого", "березня",
            "квітня", "травня", "червня",
            "липня", "серпня", "вересня",
            "жовтня", "листопада", "грудня"
        ]

        date_buttons = []

        for i in range(1, 5):
            date = today + timedelta(days=i)

            label = (
                f"{weekdays[date.weekday()]}, "
                f"{date.day} {months[date.month]}"
            )

            date_buttons.append(
                InlineKeyboardButton(
                    label,
                    callback_data="date_" + date.strftime("%Y-%m-%d")
                )
            )

        keyboard = [
            date_buttons[i:i + 2]
            for i in range(0, len(date_buttons), 2)
        ]

        keyboard.append([
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="cancel"
            )
        ])

        await query.message.reply_text(
            f"✂️ {services[data]}\n\n"
            "📅 Оберіть зручний день:",
            reply_markup=InlineKeyboardMarkup(keyboard)

        )

    # DATE
    elif data.startswith("date_"):

        selected_date = data.replace("date_", "")
        context.user_data["date"] = selected_date

        ai_state = context.user_data.get("ai_state", {})
        ai_state["date"] = selected_date
        context.user_data["ai_state"] = ai_state

        available_times = []

        for hour in range(10, 20):
            selected_time = f"{hour:02d}:00"

            if not slot_is_taken(selected_date, selected_time):
                available_times.append(selected_time)

        if not available_times:
            await query.message.reply_text(
                "😔 На цей день вільного часу немає. Оберіть інший день."
            )
            return

        keyboard = [
            [
                InlineKeyboardButton(
                    selected_time,
                    callback_data=f"time_{selected_time}"
                )
            ]
            for selected_time in available_times
        ]

        keyboard.append([
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="cancel"
            )
        ])

        await query.message.reply_text(
            f"📅 Обрано дату: {selected_date}\n\n"
            "🕒 Оберіть вільний час:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # TIME

    elif data.startswith("time_"):

        selected_time = data.replace(
            "time_",
            ""
        )

        context.user_data["time"] = selected_time

        service = context.user_data.get(
            "service"
        )

        date = context.user_data.get(
            "date"
        )

        keyboard = [
            [InlineKeyboardButton(
                "✅ Підтвердити",
                callback_data="confirm"
            )],
            [InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="cancel"
            )]
        ]

        await query.message.reply_text(
            "📋 Ваш запис:\n\n"
            f"✂️ {service}\n"
            f"📅 {date}\n"
            f"🕐 {selected_time}\n\n"
            "Підтверджуєте?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # CONFIRM

    elif data == "confirm":

        user = query.from_user

        service = context.user_data.get(
            "service"
        )

        date = context.user_data.get(
            "date"
        )

        time = context.user_data.get(
            "time"
        )

        if not service or not date or not time:

            await query.message.reply_text(
                "⚠️ Дані запису втрачено. "
                "Почніть запис заново."
            )
            return

        # Important: re-check before saving.
        if slot_is_taken(date, time):

            await query.message.reply_text(
                "😔 Цей час щойно зайняли.\n"
                "Будь ласка, оберіть інший."
            )

            context.user_data.clear()
            return

        save_booking(
            user.id,
            user.full_name,
            service,
            date,
            time
        )

        await query.message.reply_text(
            "✅ Запис підтверджено!\n\n"
            f"✂️ {service}\n"
            f"📅 {date}\n"
            f"🕐 {time}\n\n"
            "До зустрічі! 👋"
        )

        context.user_data.clear()

    # CANCEL

    elif data == "cancel":

        context.user_data.clear()

        await query.message.reply_text(
            "❌ Запис скасовано."
        )

    # ADMIN DELETE

    elif data.startswith("admin_delete_"):

        if query.from_user.id != ADMIN_ID:

            await query.message.reply_text(
                "⛔ У вас немає доступу."
            )
            return

        booking_id = int(
            data.replace(
                "admin_delete_",
                ""
            )
        )

        delete_booking(booking_id)

        await query.edit_message_text(
            f"🗑 Запис #{booking_id} скасовано."
        )

# =========================
# AI MANAGER
# =========================

BUSINESS_INFO = """
Ти AI-адміністратор тестового барбершопу Barber Demo у Львові.

Інформація про бізнес:

Послуги:
- Стрижка — 500 грн
- Борода — 300 грн
- Стрижка + борода — 700 грн

Графік:
Понеділок–субота: 10:00–20:00
Неділя: вихідний.

Місто: Львів.

Правила:
- Відповідай українською.
- Будь коротким і дружнім.
- Не вигадуй інформацію.
- Не вигадуй вільні години.
- Якщо клієнт хоче записатися, скажи, що можеш
  допомогти з бронюванням.
- Якщо інформації немає, скажи, що уточниш її
  у адміністратора.
"""

async def ai_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_text = update.message.text
    
    # Підтвердження скасування існуючих записів
    pending_cancel_ids = context.user_data.get("pending_cancel_ids")

    if pending_cancel_ids:
        answer = user_text.strip().lower()

        if answer in ("так", "так.", "yes", "ага", "підтверджую"):
            for booking_id in pending_cancel_ids:
                delete_booking(booking_id)

            count = len(pending_cancel_ids)

            context.user_data.pop("pending_cancel_ids", None)
            context.user_data.pop("ai_state", None)

            await update.message.reply_text(
                f"✅ Скасовано записів: {count}."
            )
            return

        if answer in ("ні", "ні.", "no", "не", "залишити"):
            context.user_data.pop("pending_cancel_ids", None)

            await update.message.reply_text(
                "👍 Добре, записи залишаються."
            )
            return

    try:
        # Пам'ять поточної розмови
        state = context.user_data.get("ai_state", {})

        # AI перетворює людську мову на структуровані дані
        result = await ai_manager.understand_message(
            user_text,
            state
        )

        print("AI UNDERSTOOD:", result)
 
        intent = result.get("intent")
        service = result.get("service")
        date = result.get("date")
        time = result.get("time")
        after_time = result.get("after_time")
    
    
    # ==========================================
    # РОЗУМІННЯ ЛЮДСЬКОГО ЧАСУ
    # ==========================================
    
        import re
    
            # Якщо AI не витягнув час — пробуємо знайти його самі
        if not time:
            text_lower = user_text.lower()

            patterns = [
                r"(?:о|об|в|на)\s+(\d{1,2})(?::(\d{2}))?",
                r"(\d{1,2}):(\d{2})",
            ]

            for pattern in patterns:
                match = re.search(pattern, text_lower)

                if match:
                    hour = int(match.group(1))
                    minute = int(match.group(2) or 0)

                    # Для запису в барбершоп:
                    # 1,2,3,4,5,6,7 -> 13:00-19:00
                    if 1 <= hour <= 7:
                        hour += 12

                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        time = f"{hour:02d}:{minute:02d}"
                        break
    
    
            # Якщо AI повернув час, наприклад 04:00
        if time:
            try:
                parts = str(time).split(":")

                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0

                if 1 <= hour <= 7:
                    hour += 12

                time = f"{hour:02d}:{minute:02d}"

            except (ValueError, AttributeError, IndexError):
                time = None
    
    
        print("NORMALIZED TIME:", time)
            
                # Запам'ятовуємо нову інформацію
        if service:
            state["service"] = service

        if date:
            state["date"] = date

        if time:
            state["time"] = time

        if after_time:
            state["after_time"] = after_time

        context.user_data["ai_state"] = state

        service = state.get("service")
        date = state.get("date")
        time = state.get("time")
        after_time = state.get("after_time")

        service_names = {
            "haircut": "Стрижка",
            "beard": "Борода",
            "combo": "Стрижка + борода"
        }
    
        # -----------------------------
        # МОЇ ЗАПИСИ
        # -----------------------------

        if intent == "my_bookings":
            bookings = get_user_bookings(
                update.effective_user.id
            )

            if not bookings:
                await update.message.reply_text(
                    "📭 У вас поки немає записів."
                )
                return

            # Залишаємо тільки актуальні записи
            now = datetime.now()
            active_bookings = []

            for booking in bookings:
                booking_id, booking_service, booking_date, booking_time = booking

                try:
                    booking_dt = datetime.strptime(
                        f"{booking_date} {booking_time}",
                        "%Y-%m-%d %H:%M"
                    )

                    if booking_dt >= now:
                        active_bookings.append(booking)

                except ValueError:
                    continue

            if not active_bookings:
                await update.message.reply_text(
                    "📭 У вас немає майбутніх записів."
                )
                return

            text = "📋 Ваші записи:\n\n"

            for booking in active_bookings:
                booking_id, booking_service, booking_date, booking_time = booking

                text += (
                    f"✂️ {booking_service}\n"
                    f"📅 {booking_date}\n"
                    f"🕒 {booking_time}\n\n"
                )

            await update.message.reply_text(text)
            return

        # ----------------------------
        # СКАСУВАННЯ ІСНУЮЧОГО ЗАПИСУ
        # ----------------------------

        if intent == "cancel_booking":
            bookings = get_user_bookings(update.effective_user.id)

            # Фільтруємо за датою
            if date:
                bookings = [
                    b for b in bookings
                    if b[2] == date
                ]

            # Фільтруємо за послугою
            if service:
                bookings = [
                    b for b in bookings
                    if b[1] == service
                ]

            # Фільтруємо за часом
            if time:
                bookings = [
                    b for b in bookings
                    if b[3] == time
                ]

            if not bookings:
                await update.message.reply_text(
                    "📭 Не знайшов записів, які підходять під ваш запит."
                )
                return

            # Запам'ятовуємо ID записів для підтвердження
            context.user_data["pending_cancel_ids"] = [
                b[0] for b in bookings
            ]

            text = "🗑 Знайдено записи для скасування:\n\n"

            for booking in bookings:
                booking_id, booking_service, booking_date, booking_time = booking

                name = service_names.get(
                    booking_service,
                    booking_service
                )

                text += (
                    f"✂️ {name}\n"
                    f"📅 {booking_date}\n"
                    f"🕒 {booking_time}\n\n"
                )

            text += (
                f"Знайдено: {len(bookings)}\n\n"
                "❓ Скасувати ці записи?\n"
                "Напишіть «так» або «ні»."
            )

            await update.message.reply_text(text)
            return

        # ---------------------------
        # ПЕРЕНЕСЕННЯ ІСНУЮЧОГО ЗАПИСУ
        # ---------------------------

        if intent == "reschedule_booking":
            bookings = get_user_bookings(update.effective_user.id)

            # Шукаємо запис, який користувач хоче перенести
            matching_bookings = []

            for booking in bookings:
                booking_id, booking_service, booking_date, booking_time = booking

                if date and booking_date != date:
                    continue

                if service:
                    expected_service = service_names.get(service, service)

                    if booking_service != expected_service:
                        continue

                matching_bookings.append(booking)

            if not matching_bookings:
                await update.message.reply_text(
                    "📭 Не знайшов запису, який можна перенести."
                )
                return

            if len(matching_bookings) > 1:
                await update.message.reply_text(
                    "🤔 На цю дату у вас декілька записів.\n"
                    "Напишіть час запису, який потрібно перенести."
                )
                return
            # Знайдений один конкретний запис
            booking = matching_bookings[0]

            booking_id = booking[0]
            old_service = booking[1]
            old_date = booking[2]
            old_time = booking[3]

            # Час із повідомлення — це НОВИЙ час
            new_time = time
            new_date = date

            if not new_time:
                await update.message.reply_text(
                    "🕒 На котру годину перенести запис?"
                )
                return

            if not new_date:
                new_date = old_date

            # Перевіряємо новий слот
            if slot_is_taken(new_date, new_time):
                await update.message.reply_text(
                    f"😕 {new_date} о {new_time} вже зайнято.\n"
                    "Оберіть інший час."
                )
                return

            # Запам'ятовуємо перенесення до підтвердження
            context.user_data["pending_reschedule"] = {
                "booking_id": booking_id,
                "old_date": old_date,
                "old_time": old_time,
                "new_date": new_date,
                "new_time": new_time
            }

            name = service_names.get(old_service, old_service)

            await update.message.reply_text(
                f"🔄 Перенести запис?\n\n"
                f"✂️ {name}\n"
                f"📅 {old_date} о {old_time}\n"
                f"⬇️\n"
                f"📅 {new_date} о {new_time}\n\n"
                f"Напишіть «так» або «ні»."
            )
            return

        # ---------------------------
        # СКАСУВАННЯ ДІАЛОГУ
        # ---------------------------

        if intent == "cancel":
            context.user_data.pop("ai_state", None)

            await update.message.reply_text(
                "❌ Добре, запис скасовано."
            )
            return

    
            # -------------------------
            # ПІДТВЕРДЖЕННЯ
            # -------------------------
    
            if intent == "confirm":

                pending_reschedule = context.user_data.get("pending_reschedule")

                if pending_reschedule:
                    booking_id = pending_reschedule["booking_id"]
                    new_date = pending_reschedule["new_date"]
                    new_time = pending_reschedule["new_time"]

                    if slot_is_taken(new_date, new_time):
                        await update.message.reply_text(
                            "😔 Цей час уже зайнятий. Оберіть інший час."
                        )
                        return

                    update_booking_datetime(
                        booking_id,
                        new_date,
                        new_time
                    )

                    context.user_data.pop("pending_reschedule", None)
                    context.user_data.pop("ai_state", None)

                    await update.message.reply_text(
                        "✅ Запис успішно перенесено!\n\n"
                        f"📅 {new_date}\n"
                        f"🕒 {new_time}"
                    )
                    return
    
                if not service or not date or not time:
                    await update.message.reply_text(
                        "Поки не маю всіх даних для запису."
                    )
                    return
    
                if slot_is_taken(date, time):
                    await update.message.reply_text(
                        "😔 Цей час уже зайнятий. "
                        "Оберіть інший."
                    )
                    return
    
                user = update.effective_user
    
                save_booking(
                    user.id,
                    user.full_name,
                    service_names.get(service, service),
                    date,
                    time
                )
    
                await update.message.reply_text(
                    "✅ Готово! Ви записані.\n\n"
                    f"✂️ {service_names.get(service, service)}\n"
                    f"📅 {date}\n"
                    f"🕐 {time}\n\n"
                    "До зустрічі! 👋"
                )
    
                context.user_data.pop("ai_state", None)
                return
    
            # -------------------------
            # КЛІЄНТ ОБРАВ ЧАС
            # -------------------------
    
            if intent == "choose_time" and time:
    
                if not service or not date:
                    await update.message.reply_text(
                        "Уточніть, будь ласка, "
                        "послугу та день запису."
                    )
                    return
    
                if slot_is_taken(date, time):
                    await update.message.reply_text(
                        "😔 На жаль, цей час уже зайнятий."
                    )
                    return
    
                state["time"] = time
                context.user_data["ai_state"] = state
    
                await update.message.reply_text(
                    "📋 Підтверджуємо запис?\n\n"
                    f"✂️ {service_names.get(service, service)}\n"
                    f"📅 {date}\n"
                    f"🕐 {time}\n\n"
                    "Напишіть «так» для підтвердження."
                )
                return
    
            # -------------------------
            # ПОШУК ВІЛЬНОГО ЧАСУ
            # -------------------------
    
            if intent == "booking":

                if not service:
                    await update.message.reply_text(
                        "✂️ Що бажаєте зробити?\n\n"
                        "Стрижка — 500 грн\n"
                        "Борода — 300 грн\n"
                        "Стрижка + борода — 700 грн"
                    )

                    state["date"] = date
                    state["time"] = time
                    context.user_data["ai_state"] = state
                    return

                # Визначаємо бізнес
                client_business_id = context.user_data.get(
                    "client_business_id"
                )
    
                if client_business_id:
                    business = get_business_by_id(
                        client_business_id
                    )
                else:
                    business = get_business_by_owner(
                        update.effective_user.id
                    )
    
                if not business:
                    await update.message.reply_text(
                        "⚠️Не вдалося визначити бізнес для запису."
                    )
                    return
    
                if not service:
                    await update.message.reply_text(
                        "Що бажаєте зробити?\n\n"
                        "✂️Стрижка\n"
                        "🧔Борода\n"
                        "✂️Стрижка + борода"
                    )
                    return
    
                if not date:
                    await update.message.reply_text(
                        "На який день хочете записатися?"
                    )
                    return
    
                # Якщо дату ще не визначено
                if not date:
                    await update.message.reply_text(
                        "На який день хочете записатися?"
                    )
                    return
    
                # Визначаємо день тижня
                booking_date = datetime.strptime(
                    date,
                    "%Y-%m-%d"
                )
    
                weekday = booking_date.weekday()
    
                # Беремо графік конкретного бізнесу
                working_hours = get_working_hours(
                    business["id"]
                )
    
                day_schedule = next(
                    (
                        row
                        for row in working_hours
                        if row["weekday"] == weekday
                    ),
                    None
                )
    
                # Перевіряємо, чи бізнес працює цього дня
                if (
                    not day_schedule
                    or not day_schedule["is_open"]
                ):
                    await update.message.reply_text(
                        "😔 У цей день ми не працюємо.\n"
                        "Оберіть, будь ласка, інший день."
                    )
                    return
    
                start_time = datetime.strptime(
                    day_schedule["start_time"],
                    "%H:%M"
                )
    
                end_time = datetime.strptime(
                    day_schedule["end_time"],
                    "%H:%M"
                )
    
                # Генеруємо слоти кожні 60 хвилин
                available_times = []
                current_time = start_time
    
                while current_time < end_time:
                    available_times.append(
                        current_time.strftime("%H:%M")
                    )
                    current_time += timedelta(minutes=60)
    
                # Прибираємо зайняті години
                available_times = [
                    t
                    for t in available_times
                    if not slot_is_taken(date, t)
                ]
    
                # Наприклад: "після 16"
                if after_time:
                    available_times = [
                        t
                        for t in available_times
                        if t >= after_time
                    ]
    
                if not available_times:
                    await update.message.reply_text(
                        "😔 На цей день відповідного "
                        "вільного часу немає."
                    )
                    return
    
                times_text = "\n".join(
                    f"🕐 {t}"
                    for t in available_times
                )
    
                await update.message.reply_text(
                    f"На {date} доступно:\n\n"
                    f"{times_text}\n\n"
                    "Напишіть зручний час."
                )
                return
    
            # -------------------------
            # ЗВИЧАЙНА РОЗМОВА
            # -------------------------
    
           
            client_business_id = context.user_data.get(
                "client_business_id"
            )
    
            if client_business_id:
                # CLIENT MODE
                business = get_business_by_id(
                    client_business_id
                )
    
            else:
                # Перевіряємо, чи користувач є власником
                business = get_business_by_owner(
                    update.effective_user.id
                )
    
            if business:
                dynamic_business_info = build_business_prompt(
                    business["id"]
                )
            else:
                dynamic_business_info = BUSINESS_INFO
    
            response = await client.responses.create(
                model="gpt-5.6-luna",
                instructions=dynamic_business_info,
                input=user_text
            )
    
            await update.message.reply_text(
                response.output_text
            )
    except Exception as error:
        print("AI ERROR:", error)

        await update.message.reply_text(
            "⚠️ Зараз не можу відповісти. "
            "Спробуйте ще раз."
        )

# =========================
# MAIN
# ========================

async def mylink(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    business = get_business_by_owner(user_id)

    if not business:
        await update.message.reply_text(

            "⚠️ У вас ще немає створеного бізнесу.\n\n"
            "Спочатку використайте /setup."
        )
        return

    business_id = business["id"]

    bot_username = context.bot.username

    client_link = (
        f"https://t.me/{bot_username}"
        f"?start=business_{business_id}"
    )

    await update.message.reply_text(
        f"🔗 Посилання вашого бізнесу:\n\n"
        f"{client_link}\n\n"
        f"👤 Надішліть його клієнтам або "
        f"додайте в Instagram, TikTok чи на сайт."
    )

def main():
    init_db()

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        get_setup_handler()
    )

    app.add_handler(
        get_schedule_handler()
    )

    app.add_handler(
        get_addservice_handler()
    )

    app.add_handler(
        get_services_handler()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )
    app.add_handler(
        CommandHandler(
            "mylink",
            mylink
        )
    )
    app.add_handler(
        CommandHandler(
            "mybookings",
            my_bookings_command
        )
    )

    app.add_handler(
        CommandHandler(
            "admin",
            admin
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            ai_message
         )
    )
    

    print("BizBot v0.4   запущений!")

    app.run_polling()


if __name__ == "__main__":
    main()
