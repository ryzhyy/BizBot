"""
Панель власника платформи (не власника окремого бізнесу, а того,
хто адмініструє весь BizBot). Лише перегляд — жодна дія тут не
змінює дані жодного бізнесу.

Доступ визначається через OWNER_TELEGRAM_ID у .env, а не хардкодиться
в коді, бо репозиторій публічний.
"""
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

from database import get_working_hours, DAY_NAMES
from business_context import (
    get_all_businesses,
    get_business_by_id,
    get_business_services,
    get_faq_items,
    get_business_maps_link,
)
from bookings import get_business_bookings


OWNER_TELEGRAM_ID = os.getenv("OWNER_TELEGRAM_ID")

MAX_BOOKINGS_SHOWN = 10


def is_platform_owner(telegram_id):
    if not OWNER_TELEGRAM_ID:
        return False

    try:
        return int(telegram_id) == int(OWNER_TELEGRAM_ID)
    except (TypeError, ValueError):
        return False


def _access_denied_text():
    return "⛔ Ця команда доступна лише власнику платформи."


def _format_hours(business_id):
    rows = get_working_hours(business_id)

    if not rows:
        return "  графік не налаштовано\n"

    lines = ""

    for row in rows:
        day = DAY_NAMES[row["weekday"]]

        if row["is_open"]:
            lines += f"  {day}: {row['start_time']}–{row['end_time']}\n"
        else:
            lines += f"  {day}: вихідний\n"

    return lines


def _business_list_message():
    businesses = get_all_businesses()

    if not businesses:
        return "📭 На платформі ще немає жодного бізнесу.", None

    keyboard = [
        [InlineKeyboardButton(
            f"🏢 {business['name']} "
            f"({business['category'] or 'без категорії'})",
            callback_data=f"platform_biz_{business['id']}"
        )]
        for business in businesses
    ]

    text = (
        f"👑 Панель власника платформи\n\n"
        f"Усього бізнесів: {len(businesses)}\n\n"
        "Оберіть бізнес, щоб переглянути деталі:"
    )

    return text, InlineKeyboardMarkup(keyboard)


def _business_detail_message(business_id):
    business = get_business_by_id(business_id)

    if not business:
        return "⚠️ Бізнес не знайдено.", None

    services = get_business_services(business_id)

    services_text = "".join(
        f"  ✂️ {service['name']} — {service['price']} грн "
        f"({service['duration']} хв)\n"
        for service in services
    ) or "  Послуги не додані\n"

    faq_count = len(get_faq_items(business_id))

    bookings = get_business_bookings(business_id)

    bookings_text = "".join(
        f"  #{booking['id']} {booking['customer_name']} — "
        f"{booking['service']}, {booking['date']} о {booking['time']}\n"
        for booking in bookings[:MAX_BOOKINGS_SHOWN]
    ) or "  Немає майбутніх записів\n"

    if len(bookings) > MAX_BOOKINGS_SHOWN:
        bookings_text += f"  ...і ще {len(bookings) - MAX_BOOKINGS_SHOWN}\n"

    maps_link = get_business_maps_link(business)
    location_line = f"📍 {maps_link}\n" if maps_link else ""

    contact_line = ""

    if (
        business["support_contact_mode"] == "manual"
        and business["support_contact_value"]
    ):
        contact_line = f"☎️ {business['support_contact_value']}\n"
    elif business["phone"]:
        contact_line = f"☎️ {business['phone']}\n"

    text = (
        f"🏢 {business['name']}\n"
        f"🏷 Категорія: {business['category'] or 'не вказано'}\n"
        f"🌆 Місто: {business['city'] or 'не вказано'}\n"
        f"👤 Власник (Telegram ID): {business['owner_telegram_id']}\n"
        f"{contact_line}"
        f"{location_line}\n"
        f"🧾 Послуги ({len(services)}):\n{services_text}\n"
        f"🗓 Графік:\n{_format_hours(business_id)}\n"
        f"❓ FAQ: {faq_count} записів\n\n"
        f"📅 Майбутні записи ({len(bookings)}):\n{bookings_text}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⬅️ До списку бізнесів",
            callback_data="platform_back"
        )
    ]])

    return text, keyboard


async def platform_start(update, context):
    if not is_platform_owner(update.effective_user.id):
        await update.message.reply_text(_access_denied_text())
        return

    text, keyboard = _business_list_message()
    await update.message.reply_text(text, reply_markup=keyboard)


async def platform_business_detail(update, context):
    query = update.callback_query
    await query.answer()

    if not is_platform_owner(query.from_user.id):
        await query.message.reply_text(_access_denied_text())
        return

    business_id = int(query.data.replace("platform_biz_", ""))
    text, keyboard = _business_detail_message(business_id)

    await query.message.reply_text(text, reply_markup=keyboard)


async def platform_back_to_list(update, context):
    query = update.callback_query
    await query.answer()

    if not is_platform_owner(query.from_user.id):
        await query.message.reply_text(_access_denied_text())
        return

    text, keyboard = _business_list_message()
    await query.message.reply_text(text, reply_markup=keyboard)


def get_platform_handlers():
    return [
        CommandHandler("platform", platform_start),
        CallbackQueryHandler(
            platform_business_detail, pattern=r"^platform_biz_\d+$"
        ),
        CallbackQueryHandler(
            platform_back_to_list, pattern=r"^platform_back$"
        ),
    ]
