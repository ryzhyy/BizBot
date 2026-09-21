import os
from datetime import datetime, timedelta

from setup import get_setup_handler
from schedule import get_schedule_handler
from database import init_database

from business_context import (
    build_business_prompt,
    get_business_by_owner,
    get_business_by_id,
    get_business_services,
    get_service_by_id,
)
from services import (
    services_list,
    get_addservice_handler,
    get_services_handler,
)
from contact_settings import (
    get_setcontact_handler,
    get_setfaq_handler,
)
from bookings import (
    get_customer,
    get_or_create_customer,
    set_customer_phone,
    is_slot_taken,
    get_available_times,
    create_booking,
    get_customer_bookings,
    get_business_bookings,
    get_booking_with_customer,
    get_upcoming_bookings_needing_reminder,
    cancel_booking,
    mark_booking_completed,
    mark_reminder_sent,
    reschedule_booking,
)
from ai_manager import AIManager
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
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

def resolve_current_business(context, telegram_id):
    owned_business = get_business_by_owner(telegram_id)

    if owned_business:
        return owned_business

    client_business_id = context.user_data.get("client_business_id")

    if client_business_id:
        return get_business_by_id(client_business_id)

    return None


# =========================
# REPLY KEYBOARDS
# =========================

OWNER_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📝 Мій бізнес", "➕ Додати послугу"],
        ["📋 Мої послуги", "❓ Допомога"],
        ["⚙️ Контакт для клієнтів", "❓ Налаштувати FAQ"],
    ],
    resize_keyboard=True
)

CLIENT_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["✂️ Записатися", "🧾 Послуги та ціни"],
        ["📅 Мої записи", "❓ Допомога"],
    ],
    resize_keyboard=True
)

REPLY_MENU_BUTTON_TEXTS = [
    "📝 Мій бізнес",
    "📋 Мої послуги",
    "✂️ Записатися",
    "🧾 Послуги та ціни",
    "📅 Мої записи",
    "❓ Допомога",
]


async def start_booking(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = resolve_current_business(
        context, update.effective_user.id
    )

    if not business:
        await update.message.reply_text(
            "⚠️ Не вдалося визначити бізнес для запису."
        )
        return

    services = get_business_services(business["id"])

    if not services:
        await update.message.reply_text(
            "😔 У цього бізнесу ще немає доданих послуг."
        )
        return

    context.user_data["booking_business_id"] = business["id"]

    keyboard = [
        [InlineKeyboardButton(
            f"✂️ {service['name']} — {service['price']} грн",
            callback_data=f"service_{service['id']}"
        )]
        for service in services
    ]

    await update.message.reply_text(
        "Оберіть послугу:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_business_services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = resolve_current_business(
        context, update.effective_user.id
    )

    if not business:
        await update.message.reply_text(
            "⚠️ Не вдалося визначити бізнес."
        )
        return

    services = get_business_services(business["id"])

    if not services:
        await update.message.reply_text(
            "😔 У цього бізнесу ще немає доданих послуг."
        )
        return

    text = f"🧾 Послуги «{business['name']}»:\n\n"

    for service in services:
        text += f"✂️ {service['name']} — {service['price']} грн\n"

    await update.message.reply_text(text)


async def show_my_business(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⚠️ У вас ще немає створеного бізнесу.\n\n"
            "Спочатку створіть бізнес через /setup."
        )
        return

    services = get_business_services(business["id"])

    if services:
        services_text = "\n".join(
            f"✂️ {service['name']} — {service['price']} грн"
            for service in services
        )
    else:
        services_text = "Послуги ще не додані"

    bot_username = context.bot.username

    client_link = (
        f"https://t.me/{bot_username}"
        f"?start=business_{business['id']}"
    )

    active_bookings_count = len(
        get_business_bookings(business["id"])
    )

    city = business["city"] or "не вказано"

    await update.message.reply_text(
        f"🏢 {business['name']}\n"
        f"📍 {city}\n\n"
        f"🧾 Послуги:\n{services_text}\n\n"
        f"🔗 Посилання для клієнтів:\n{client_link}\n\n"
        f"📊 Активних записів: {active_bookings_count}"
    )


async def build_client_help_text(business, context):
    contact_mode = business["support_contact_mode"] if business else None
    contact_value = business["support_contact_value"] if business else None

    contact_line = None

    if contact_mode == "manual" and contact_value:
        contact_line = f"☎️ {contact_value}"

    elif business:
        try:
            owner_chat = await context.bot.get_chat(
                business["owner_telegram_id"]
            )

            if owner_chat.username:
                contact_line = (
                    "👤 Написати власнику: "
                    f"https://t.me/{owner_chat.username}"
                )
            else:
                contact_line = (
                    "👤 Написати власнику напряму: "
                    f"tg://user?id={business['owner_telegram_id']}"
                )

        except Exception as error:
            print("GET CHAT ERROR:", error)

    if not contact_line:
        phone = business["phone"] if business else None

        contact_line = (
            f"☎️ {phone}"
            if phone
            else "☎️ Телефон ще не вказано, зверніться через AI-чат"
        )

    faq_text = business["faq_text"] if business else None

    faq_section = faq_text or (
        "• Записатися — кнопка «✂️ Записатися» або напишіть, "
        "наприклад «хочу стрижку завтра о 17:00»\n"
        "• Скасувати запис — напишіть «скасуй мій запис»\n"
        "• Перенести запис — напишіть «перенеси мій запис на ...»"
    )

    return (
        "🆘 Допомога\n\n"
        f"{contact_line}\n\n"
        "❓ FAQ:\n\n"
        f"{faq_section}"
    )


async def help_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    telegram_id = update.effective_user.id
    owned_business = get_business_by_owner(telegram_id)

    if owned_business:
        await update.message.reply_text(
            "🆘 Підтримка\n\n"
            "З питань роботи бота: @your_support_username\n\n"
            "❓ FAQ власника:\n\n"
            "• Додати послугу — кнопка «➕ Додати послугу» "
            "або /addservice\n"
            "• Отримати посилання для клієнтів — /mylink\n"
            "• Переглянути свої записи — /admin"
        )
        return

    business = resolve_current_business(context, telegram_id)

    text = await build_client_help_text(business, context)

    await update.message.reply_text(text)


async def reply_keyboard_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text

    if text == "📝 Мій бізнес":
        await show_my_business(update, context)
    elif text == "📋 Мої послуги":
        await services_list(update, context)
    elif text == "✂️ Записатися":
        await start_booking(update, context)
    elif text == "🧾 Послуги та ціни":
        await show_business_services(update, context)
    elif text == "📅 Мої записи":
        await my_bookings_command(update, context)
    elif text == "❓ Допомога":
        await help_button(update, context)



async def notify_owner_of_booking(
    context, business, customer_name, service_name, date, time
):
    if not business:
        return

    owner_telegram_id = business["owner_telegram_id"]

    if not owner_telegram_id:
        return

    try:
        await context.bot.send_message(
            chat_id=owner_telegram_id,
            text=(
                "🔔 Новий запис!\n\n"
                f"👤 {customer_name}\n"
                f"✂️ {service_name}\n"
                f"📅 {date}\n"
                f"🕒 {time}"
            )
        )
    except Exception as error:
        print("OWNER NOTIFY ERROR:", error)


REQUEST_PHONE_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📱 Поділитися номером", request_contact=True)],
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)


async def require_customer_phone(update, context, business_id):
    user = update.effective_user

    customer = get_customer(business_id, user.id)

    if not customer:
        get_or_create_customer(business_id, user.id, user.full_name)
        customer = get_customer(business_id, user.id)

    if customer["phone"]:
        return True

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "Для завершення запису поділіться, будь ласка, "
            "номером телефону 👇"
        ),
        reply_markup=REQUEST_PHONE_KEYBOARD
    )

    return False


async def finalize_booking(
    update, context, business, service_id, service_name,
    date, time, success_text
):
    user = update.effective_user

    customer_id = get_or_create_customer(
        business["id"], user.id, user.full_name
    )

    create_booking(
        business["id"],
        customer_id,
        service_id,
        date,
        time
    )

    await notify_owner_of_booking(
        context,
        business,
        user.full_name,
        service_name,
        date,
        time
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=success_text,
        reply_markup=CLIENT_MENU_KEYBOARD
    )

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

                await update.message.reply_text(
                    "Або скористайтесь меню нижче 👇",
                    reply_markup=CLIENT_MENU_KEYBOARD
                )
                return

            except (ValueError, TypeError):
                await update.message.reply_text(
                    "⚠️ Некоректне посилання на бізнес."
                )
                return

    # Звичайний /start без business ID —
    # прибираємо попередній client-контекст, якщо він був
    context.user_data.pop("client_business_id", None)

    await update.message.reply_text(
        "👋 Вітаю у BizBot!\n\n"
        "Якщо ви власник бізнесу — використайте /setup.\n\n"
        "Якщо ви клієнт — відкрийте персональне "
        "посилання потрібного бізнесу.",
        reply_markup=OWNER_MENU_KEYBOARD
    )

# =========================
# MY BOOKINGS
# =========================

async def my_bookings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    business = resolve_current_business(
        context, update.effective_user.id
    )

    customer = (
        get_customer(business["id"], update.effective_user.id)
        if business else None
    )

    bookings = (
        get_customer_bookings(business["id"], customer["id"])
        if customer else []
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

    business = get_business_by_owner(update.effective_user.id)

    if not business:
        await update.message.reply_text(
            "⛔ У вас немає власного бізнесу. Спробуйте /setup."
        )
        return

    bookings = get_business_bookings(business["id"])

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
            ),
            InlineKeyboardButton(
                "✅ Виконано",
                callback_data=f"admin_complete_{booking_id}"
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

    # START MENU

    if data == "owner_start":
        await query.message.reply_text(
            "🏢 Створімо ваш бізнес!\n\n"
            "Натисніть /setup, щоб почати налаштування."
        )
        return

    elif data == "client_start":
        await query.message.reply_text(
            "👤 Щоб записатися, відкрийте персональне "
            "посилання потрібного бізнесу.\n\n"
            "Після цього ви зможете обрати послугу, "
            "дату та час."
        )
        return

    # BUSINESS SETUP BUTTONS

    elif data == "setup_add_service":
        await query.message.reply_text(
            "➕ Додаємо наступну послугу.\n\n"
            "Натисніть /addservice"
        )
        return

    elif data == "setup_finish":
        business = get_business_by_owner(query.from_user.id)

        if not business:
            await query.message.reply_text(
                "⚠️ Бізнес не знайдено. Спробуйте /setup."
            )
            return

        business_id = business["id"]
        business_name = business["name"]

        bot_info = await context.bot.get_me()
        client_link = (
            f"https://t.me/{bot_info.username}"
            f"?start=business_{business_id}"
        )

        await query.message.reply_text(
            "🎉 Налаштування завершено!\n\n"
            f"🏢 {business_name}\n\n"
            "🔗 Персональне посилання для клієнтів:\n"
            f"{client_link}\n\n"
            "Надішліть це посилання клієнтам — "
            "через нього вони зможуть записуватися."
        )
        return

    # SERVICES

    elif data == "services":

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

        business = resolve_current_business(
            context, query.from_user.id
        )

        customer = (
            get_customer(business["id"], query.from_user.id)
            if business else None
        )

        bookings = (
            get_customer_bookings(business["id"], customer["id"])
            if customer else []
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

        business = resolve_current_business(
            context, query.from_user.id
        )

        if not business:
            await query.message.reply_text(
                "⚠️ Не вдалося визначити бізнес для запису."
            )
            return

        services = get_business_services(business["id"])

        if not services:
            await query.message.reply_text(
                "😔 У цього бізнесу ще немає доданих послуг."
            )
            return

        context.user_data["booking_business_id"] = business["id"]

        keyboard = [
            [InlineKeyboardButton(
                f"✂️ {service['name']} — {service['price']} грн",
                callback_data=f"service_{service['id']}"
            )]
            for service in services
        ]

        await query.message.reply_text(
            "Оберіть послугу:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # SERVICE

    elif data.startswith("service_"):

        booking_business_id = context.user_data.get("booking_business_id")

        service_id = int(data.replace("service_", ""))

        service = next(
            (
                s for s in get_business_services(booking_business_id)
                if s["id"] == service_id
            ),
            None
        ) if booking_business_id else None

        if not service:
            await query.message.reply_text(
                "⚠️ Сесію бронювання втрачено. "
                "Почніть заново через «Записатися»."
            )
            return

        context.user_data["service_id"] = service["id"]
        context.user_data["service_name"] = service["name"]

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
            f"✂️ {service['name']}\n\n"
            "📅 Оберіть зручний день:",
            reply_markup=InlineKeyboardMarkup(keyboard)

        )

    # DATE
    elif data.startswith("date_"):

        booking_business_id = context.user_data.get("booking_business_id")

        selected_date = data.replace("date_", "")
        context.user_data["date"] = selected_date

        ai_state = context.user_data.get("ai_state", {})
        ai_state["date"] = selected_date
        context.user_data["ai_state"] = ai_state

        available_times = get_available_times(
            booking_business_id, selected_date
        )

        if available_times is None:
            await query.message.reply_text(
                "😔 У цей день ми не працюємо. Оберіть інший день."
            )
            return

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

        service_name = context.user_data.get(
            "service_name"
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
            f"✂️ {service_name}\n"
            f"📅 {date}\n"
            f"🕐 {selected_time}\n\n"
            "Підтверджуєте?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # CONFIRM

    elif data == "confirm":

        booking_business_id = context.user_data.get("booking_business_id")
        service_id = context.user_data.get("service_id")
        service_name = context.user_data.get("service_name")

        date = context.user_data.get(
            "date"
        )

        time = context.user_data.get(
            "time"
        )

        if not booking_business_id or not service_id or not date or not time:

            await query.message.reply_text(
                "⚠️ Дані запису втрачено. "
                "Почніть запис заново."
            )
            return

        # Important: re-check before saving.
        if is_slot_taken(booking_business_id, date, time):

            await query.message.reply_text(
                "😔 Цей час щойно зайняли.\n"
                "Будь ласка, оберіть інший."
            )

            context.user_data.clear()
            return

        if not await require_customer_phone(
            update, context, booking_business_id
        ):
            context.user_data["pending_phone_booking"] = {
                "business_id": booking_business_id,
                "service_id": service_id,
                "service_name": service_name,
                "date": date,
                "time": time,
            }
            return

        booking_business = get_business_by_id(booking_business_id)

        await finalize_booking(
            update,
            context,
            booking_business,
            service_id,
            service_name,
            date,
            time,
            success_text=(
                "✅ Запис підтверджено!\n\n"
                f"✂️ {service_name}\n"
                f"📅 {date}\n"
                f"🕐 {time}\n\n"
                "До зустрічі! 👋"
            )
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

        business = get_business_by_owner(query.from_user.id)

        if not business:

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

        cancelled = cancel_booking(booking_id, business["id"])

        if not cancelled:
            await query.message.reply_text(
                "⚠️ Цей запис не знайдено у вашому бізнесі."
            )
            return

        await query.edit_message_text(
            f"🗑 Запис #{booking_id} скасовано."
        )

    # ADMIN COMPLETE

    elif data.startswith("admin_complete_"):

        business = get_business_by_owner(query.from_user.id)

        if not business:

            await query.message.reply_text(
                "⛔ У вас немає доступу."
            )
            return

        booking_id = int(
            data.replace(
                "admin_complete_",
                ""
            )
        )

        booking = get_booking_with_customer(booking_id, business["id"])

        if not booking:
            await query.message.reply_text(
                "⚠️ Цей запис не знайдено у вашому бізнесі."
            )
            return

        mark_booking_completed(booking_id)

        client_notified = True

        try:
            await context.bot.send_message(
                chat_id=booking["customer_telegram_id"],
                text=(
                    "✅ Роботу виконано!\n\n"
                    f"✂️ {booking['service']}\n"
                    f"📅 {booking['date']}\n"
                    f"🕒 {booking['time']}\n\n"
                    "Дякуємо, що скористались нашими послугами! "
                    "Будемо раді бачити вас знову."
                )
            )
        except Exception as error:
            client_notified = False
            print("NOTIFY CLIENT ERROR:", error)

        if client_notified:
            await query.edit_message_text(
                f"✅ Запис #{booking_id} позначено виконаним, "
                "клієнта сповіщено."
            )
        else:
            await query.edit_message_text(
                f"✅ Запис #{booking_id} позначено виконаним.\n"
                "⚠️ Не вдалося сповістити клієнта "
                "(можливо, заблокував бота)."
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
            pending_cancel_business_id = context.user_data.get(
                "pending_cancel_business_id"
            )

            for booking_id in pending_cancel_ids:
                cancel_booking(booking_id, pending_cancel_business_id)

            count = len(pending_cancel_ids)

            context.user_data.pop("pending_cancel_ids", None)
            context.user_data.pop("pending_cancel_business_id", None)
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

        current_business = resolve_current_business(
            context, update.effective_user.id
        )
        current_business_id = (
            current_business["id"] if current_business else None
        )

        # AI перетворює людську мову на структуровані дані
        result = await ai_manager.understand_message(
            user_text,
            state,
            business_id=current_business_id
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

        # -----------------------------
        # МОЇ ЗАПИСИ
        # -----------------------------

        if intent == "my_bookings":
            business = resolve_current_business(
                context, update.effective_user.id
            )

            customer = (
                get_customer(business["id"], update.effective_user.id)
                if business else None
            )

            bookings = (
                get_customer_bookings(business["id"], customer["id"])
                if customer else []
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
            business = resolve_current_business(
                context, update.effective_user.id
            )

            customer = (
                get_customer(business["id"], update.effective_user.id)
                if business else None
            )

            bookings = (
                get_customer_bookings(business["id"], customer["id"])
                if customer else []
            )

            # Фільтруємо за датою
            if date:
                bookings = [
                    b for b in bookings
                    if b[2] == date
                ]

            # Фільтруємо за послугою
            if service:
                expected_service = None

                if business:
                    found_service = get_service_by_id(
                        service, business["id"]
                    )
                    expected_service = (
                        found_service["name"] if found_service else None
                    )

                bookings = [
                    b for b in bookings
                    if b[1] == expected_service
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
            context.user_data["pending_cancel_business_id"] = business["id"]

            text = "🗑 Знайдено записи для скасування:\n\n"

            for booking in bookings:
                booking_id, booking_service, booking_date, booking_time = booking

                text += (
                    f"✂️ {booking_service}\n"
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
            business = resolve_current_business(
                context, update.effective_user.id
            )

            customer = (
                get_customer(business["id"], update.effective_user.id)
                if business else None
            )

            bookings = (
                get_customer_bookings(business["id"], customer["id"])
                if customer else []
            )

            # Шукаємо запис, який користувач хоче перенести
            expected_service = None

            if service and business:
                found_service = get_service_by_id(service, business["id"])
                expected_service = (
                    found_service["name"] if found_service else None
                )

            matching_bookings = []

            for booking in bookings:
                booking_id, booking_service, booking_date, booking_time = booking

                if date and booking_date != date:
                    continue

                if service and booking_service != expected_service:
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
            if is_slot_taken(business["id"], new_date, new_time):
                await update.message.reply_text(
                    f"😕 {new_date} о {new_time} вже зайнято.\n"
                    "Оберіть інший час."
                )
                return

            # Запам'ятовуємо перенесення до підтвердження
            context.user_data["pending_reschedule"] = {
                "booking_id": booking_id,
                "business_id": business["id"],
                "old_date": old_date,
                "old_time": old_time,
                "new_date": new_date,
                "new_time": new_time
            }

            await update.message.reply_text(
                f"🔄 Перенести запис?\n\n"
                f"✂️ {old_service}\n"
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
                reschedule_business_id = pending_reschedule["business_id"]
                new_date = pending_reschedule["new_date"]
                new_time = pending_reschedule["new_time"]

                if is_slot_taken(reschedule_business_id, new_date, new_time):
                    await update.message.reply_text(
                        "😔 Цей час уже зайнятий. Оберіть інший час."
                    )
                    return

                reschedule_booking(
                    booking_id,
                    reschedule_business_id,
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

            confirm_business = resolve_current_business(
                context, update.effective_user.id
            )

            if not confirm_business:
                await update.message.reply_text(
                    "⚠️ Не вдалося визначити бізнес для запису."
                )
                return

            if is_slot_taken(confirm_business["id"], date, time):
                await update.message.reply_text(
                    "😔 Цей час уже зайнятий. "
                    "Оберіть інший."
                )
                return

            confirm_service = get_service_by_id(
                service, confirm_business["id"]
            )

            if not confirm_service:
                await update.message.reply_text(
                    "⚠️ Не вдалося знайти обрану послугу. "
                    "Спробуйте ще раз."
                )
                return

            if not await require_customer_phone(
                update, context, confirm_business["id"]
            ):
                context.user_data["pending_phone_booking"] = {
                    "business_id": confirm_business["id"],
                    "service_id": confirm_service["id"],
                    "service_name": confirm_service["name"],
                    "date": date,
                    "time": time,
                }
                return

            await finalize_booking(
                update,
                context,
                confirm_business,
                confirm_service["id"],
                confirm_service["name"],
                date,
                time,
                success_text=(
                    "✅ Готово! Ви записані.\n\n"
                    f"✂️ {confirm_service['name']}\n"
                    f"📅 {date}\n"
                    f"🕐 {time}\n\n"
                    "До зустрічі! 👋"
                )
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

            choose_time_business = resolve_current_business(
                context, update.effective_user.id
            )

            if not choose_time_business:
                await update.message.reply_text(
                    "⚠️ Не вдалося визначити бізнес для запису."
                )
                return

            if is_slot_taken(choose_time_business["id"], date, time):
                await update.message.reply_text(
                    "😔 На жаль, цей час уже зайнятий."
                )
                return

            choose_time_service = get_service_by_id(
                service, choose_time_business["id"]
            )

            if not choose_time_service:
                await update.message.reply_text(
                    "⚠️ Не вдалося знайти обрану послугу. "
                    "Спробуйте ще раз."
                )
                return

            state["time"] = time
            context.user_data["ai_state"] = state

            await update.message.reply_text(
                "📋 Підтверджуємо запис?\n\n"
                f"✂️ {choose_time_service['name']}\n"
                f"📅 {date}\n"
                f"🕐 {time}\n\n"
                "Напишіть «так» для підтвердження."
            )
            return

        # -------------------------
        # ПОШУК ВІЛЬНОГО ЧАСУ
        # -------------------------

        if intent == "booking":

            # Визначаємо бізнес
            business = resolve_current_business(
                context, update.effective_user.id
            )

            if not business:
                await update.message.reply_text(
                    "⚠️ Не вдалося визначити бізнес для запису."
                )
                return

            if not service:
                booking_services = get_business_services(business["id"])

                if booking_services:
                    services_text = "\n".join(
                        f"✂️ {s['name']} — {s['price']} грн"
                        for s in booking_services
                    )
                else:
                    services_text = (
                        "😔 У цього бізнесу ще немає доданих послуг."
                    )

                await update.message.reply_text(
                    f"✂️ Що бажаєте зробити?\n\n{services_text}"
                )

                state["date"] = date
                state["time"] = time
                context.user_data["ai_state"] = state
                return

            if not date:
                await update.message.reply_text(
                    "На який день хочете записатися?"
                )
                return

            available_times = get_available_times(business["id"], date)

            if available_times is None:
                await update.message.reply_text(
                    "😔 У цей день ми не працюємо.\n"
                    "Оберіть, будь ласка, інший день."
                )
                return

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

            # Якщо клієнт одразу назвав конкретний вільний час —
            # не показуємо повний список, а одразу йдемо на підтвердження
            if time and time in available_times:
                booking_service = get_service_by_id(
                    service, business["id"]
                )

                if not booking_service:
                    await update.message.reply_text(
                        "⚠️ Не вдалося знайти обрану послугу. "
                        "Спробуйте ще раз."
                    )
                    return

                state["time"] = time
                context.user_data["ai_state"] = state

                await update.message.reply_text(
                    "📋 Підтверджуємо запис?\n\n"
                    f"✂️ {booking_service['name']}\n"
                    f"📅 {date}\n"
                    f"🕐 {time}\n\n"
                    "Напишіть «так» для підтвердження."
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

        business = resolve_current_business(
            context, update.effective_user.id
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


async def handle_contact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    contact = update.message.contact

    if contact.user_id != update.effective_user.id:
        await update.message.reply_text(
            "⚠️ Будь ласка, поділіться саме своїм номером телефону."
        )
        return

    pending = context.user_data.get("pending_phone_booking")

    if not pending:
        await update.message.reply_text(
            "Дякую! Але зараз немає незавершеного запису.",
            reply_markup=CLIENT_MENU_KEYBOARD
        )
        return

    business_id = pending["business_id"]

    set_customer_phone(
        business_id, update.effective_user.id, contact.phone_number
    )

    business = get_business_by_id(business_id)

    if not business:
        await update.message.reply_text(
            "⚠️ Не вдалося знайти бізнес. Почніть запис заново.",
            reply_markup=CLIENT_MENU_KEYBOARD
        )
        context.user_data.pop("pending_phone_booking", None)
        return

    # Слот могли зайняти, поки клієнт ділився контактом.
    if is_slot_taken(business_id, pending["date"], pending["time"]):
        await update.message.reply_text(
            "😔 Цей час щойно зайняли. Спробуйте ще раз.",
            reply_markup=CLIENT_MENU_KEYBOARD
        )
        context.user_data.pop("pending_phone_booking", None)
        return

    await finalize_booking(
        update,
        context,
        business,
        pending["service_id"],
        pending["service_name"],
        pending["date"],
        pending["time"],
        success_text=(
            "✅ Запис підтверджено!\n\n"
            f"✂️ {pending['service_name']}\n"
            f"📅 {pending['date']}\n"
            f"🕐 {pending['time']}\n\n"
            "До зустрічі! 👋"
        )
    )

    context.user_data.pop("pending_phone_booking", None)


# TEMP DEBUG — прибрати перед фінальним поданням заявки.
# Дозволяє перевірити клієнтський текст "Допомога" для будь-якого
# business_id без окремого Telegram-акаунта в ролі клієнта.
async def debug_client_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text(
            "Використання: /debug_client_help <business_id>"
        )
        return

    try:
        business_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "⚠️ business_id має бути числом."
        )
        return

    business = get_business_by_id(business_id)

    if not business:
        await update.message.reply_text(
            f"⚠️ Бізнес з id={business_id} не знайдено."
        )
        return

    text = await build_client_help_text(business, context)

    await update.message.reply_text(
        f"🐞 DEBUG (business_id={business_id}):\n\n{text}"
    )


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    bookings = get_upcoming_bookings_needing_reminder(hours_ahead=2)

    for booking in bookings:
        try:
            await context.bot.send_message(
                chat_id=booking["customer_telegram_id"],
                text=(
                    "⏰ Нагадування!\n\n"
                    "Ви записані:\n"
                    f"✂️ {booking['service']}\n"
                    f"📅 {booking['date']}\n"
                    f"🕒 {booking['time']}\n\n"
                    f"Чекаємо на вас у «{booking['business_name']}»!"
                )
            )
        except Exception as error:
            print("REMINDER SEND ERROR:", error)

        mark_reminder_sent(booking["id"])


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Почати / головне меню"),
        BotCommand("setup", "Створити свій бізнес"),
        BotCommand("addservice", "Додати послугу"),
        BotCommand("services", "Мої послуги"),
        BotCommand("schedule", "Налаштувати графік роботи"),
        BotCommand("setcontact", "Контакт для клієнтів"),
        BotCommand("setfaq", "Налаштувати FAQ для клієнтів"),
        BotCommand("mylink", "Посилання для клієнтів"),
        BotCommand("mybookings", "Мої записи"),
        BotCommand("admin", "Панель власника"),
    ])


def main():
    app = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
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
        get_setcontact_handler()
    )

    app.add_handler(
        get_setfaq_handler()
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

    # TEMP DEBUG — прибрати перед фінальним поданням заявки.
    app.add_handler(
        CommandHandler(
            "debug_client_help",
            debug_client_help
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Text(REPLY_MENU_BUTTON_TEXTS),
            reply_keyboard_router
        )
    )
    app.add_handler(
        MessageHandler(
            filters.CONTACT,
            handle_contact
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            ai_message
         )
    )

    app.job_queue.run_repeating(
        send_reminders,
        interval=900,
        first=10
    )

    print("BizBot v0.4   запущений!")

    app.run_polling()


if __name__ == "__main__":
    main()
