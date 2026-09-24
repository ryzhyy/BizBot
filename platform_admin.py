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
from plans import (
    PRO_FEATURES_TEXT,
    disable_pro,
    extend_pro,
    hidden_service_ids,
    is_pro,
    plan_label,
)


OWNER_TELEGRAM_ID = os.getenv("OWNER_TELEGRAM_ID")

MAX_BOOKINGS_SHOWN = 10

CLOSE_BUTTON = InlineKeyboardButton(
    "❌ Закрити",
    callback_data="platform_close"
)


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
            f"{'💎' if is_pro(business['id']) else '🆓'} {business['name']} "
            f"({business['category'] or 'без категорії'})",
            callback_data=f"platform_biz_{business['id']}"
        )]
        for business in businesses
    ]

    keyboard.append([CLOSE_BUTTON])

    text = (
        f"👑 Панель власника платформи\n\n"
        f"Усього бізнесів: {len(businesses)}\n\n"
        "Оберіть бізнес, щоб переглянути деталі:"
    )

    return text, InlineKeyboardMarkup(keyboard)


def _business_detail_message(business_id):
    business = get_business_by_id(business_id)

    if not business:
        return "⚠️ Бізнес не знайдено.", InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ До списку бізнесів",
                callback_data="platform_back"
            )],
            [CLOSE_BUTTON],
        ])

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

    hidden = len(hidden_service_ids(business_id))
    hidden_note = (
        f"  🔒 приховано від клієнтів (Free): {hidden}\n" if hidden else ""
    )

    text = (
        f"🏢 {business['name']}\n"
        f"💳 Тариф: {plan_label(business_id)}\n"
        f"🏷 Категорія: {business['category'] or 'не вказано'}\n"
        f"🌆 Місто: {business['city'] or 'не вказано'}\n"
        f"👤 Власник (Telegram ID): {business['owner_telegram_id']}\n"
        f"{contact_line}"
        f"{location_line}\n"
        f"🧾 Послуги ({len(services)}):\n{services_text}{hidden_note}\n"
        f"🗓 Графік:\n{_format_hours(business_id)}\n"
        f"❓ FAQ: {faq_count} записів\n\n"
        f"📅 Майбутні записи ({len(bookings)}):\n{bookings_text}"
    )

    plan_rows = [[
        InlineKeyboardButton(
            "💎 +14 днів Pro", callback_data=f"platform_pro_{business_id}_14"
        ),
        InlineKeyboardButton(
            "💎 +30 днів Pro", callback_data=f"platform_pro_{business_id}_30"
        ),
    ]]

    if is_pro(business_id):
        plan_rows.append([InlineKeyboardButton(
            "🆓 Вимкнути Pro", callback_data=f"platform_prooff_{business_id}"
        )])

    keyboard = InlineKeyboardMarkup(plan_rows + [
        [InlineKeyboardButton(
            "⬅️ До списку бізнесів",
            callback_data="platform_back"
        )],
        [CLOSE_BUTTON],
    ])

    return text, keyboard


async def platform_start(update, context):
    if not is_platform_owner(update.effective_user.id):
        await update.message.reply_text(_access_denied_text())
        return

    text, keyboard = _business_list_message()
    await update.message.reply_text(text, reply_markup=keyboard)


async def _show_in_place(query, text, keyboard):
    # Вся навігація панелі відбувається в ОДНОМУ повідомленні: замість
    # нового повідомлення на кожен клік редагуємо те саме, щоб чат не
    # забивався старими панелями.
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except Exception as error:
        # Той самий текст/клавіатура — Telegram відмовляє в редагуванні,
        # це не помилка, нічого не робимо.
        if "not modified" in str(error).lower():
            return
        # Повідомлення застаре/видалене — показуємо як нове.
        print("PLATFORM EDIT ERROR:", error)
        await query.message.reply_text(text, reply_markup=keyboard)


async def platform_business_detail(update, context):
    query = update.callback_query
    await query.answer()

    if not is_platform_owner(query.from_user.id):
        await query.message.reply_text(_access_denied_text())
        return

    business_id = int(query.data.replace("platform_biz_", ""))
    text, keyboard = _business_detail_message(business_id)

    await _show_in_place(query, text, keyboard)


async def platform_back_to_list(update, context):
    query = update.callback_query
    await query.answer()

    if not is_platform_owner(query.from_user.id):
        await query.message.reply_text(_access_denied_text())
        return

    text, keyboard = _business_list_message()
    await _show_in_place(query, text, keyboard)


async def platform_close(update, context):
    query = update.callback_query
    await query.answer()

    if not is_platform_owner(query.from_user.id):
        return

    try:
        await query.message.delete()
    except Exception as error:
        # Telegram не дає видаляти повідомлення, старші за 48 годин —
        # тоді просто прибираємо кнопки й позначаємо панель закритою.
        print("PLATFORM CLOSE ERROR:", error)
        try:
            await query.edit_message_text("👑 Панель закрито.")
        except Exception:
            pass


# ---------- керування тарифом (єдина «записувальна» дія панелі) ----------

async def _notify_business_owner(bot, business, text):
    try:
        await bot.send_message(chat_id=business["owner_telegram_id"], text=text)
        return True
    except Exception as error:
        print("PLAN OWNER NOTIFY ERROR:", error)
        return False


async def platform_extend_pro(update, context):
    query = update.callback_query

    if not is_platform_owner(query.from_user.id):
        await query.answer()
        return

    _, _, business_id, days = query.data.split("_")
    business_id, days = int(business_id), int(days)
    business = get_business_by_id(business_id)

    if not business:
        await query.answer("⚠️ Бізнес не знайдено.", show_alert=True)
        return

    new_until = extend_pro(business_id, days)
    until_text = new_until.strftime("%d.%m.%Y %H:%M")

    delivered = await _notify_business_owner(
        context.bot,
        business,
        f"🎉 Для «{business['name']}» активовано Pro до {until_text}!\n\n"
        f"{PRO_FEATURES_TEXT}\n\n"
        "Що можна налаштувати зараз:\n"
        "• /setfaq — часті запитання для клієнтів\n"
        "• /setlocation — локація бізнесу\n"
        "• «👀 Режим клієнта» — подивитися бота очима клієнта\n\n"
        "Ваш поточний тариф завжди видно в «💎 Мій тариф» або /plan."
    )

    await query.answer(
        f"✅ Pro до {until_text}"
        + ("" if delivered else " (власника не вдалося сповістити)"),
        show_alert=True
    )

    text, keyboard = _business_detail_message(business_id)
    await _show_in_place(query, text, keyboard)


async def platform_disable_pro(update, context):
    query = update.callback_query
    await query.answer()

    if not is_platform_owner(query.from_user.id):
        return

    business_id = int(query.data.rsplit("_", 1)[1])
    business = get_business_by_id(business_id)

    if not business:
        return

    # Перепитуємо, щоб випадковий тап не вимкнув оплачений тариф.
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Так, перевести на Free",
            callback_data=f"platform_prooffyes_{business_id}"
        )],
        [InlineKeyboardButton(
            "⬅️ Ні, назад", callback_data=f"platform_biz_{business_id}"
        )],
    ])

    await _show_in_place(
        query,
        f"🆓 Вимкнути Pro для «{business['name']}» зараз?\n\n"
        f"Зараз: {plan_label(business_id)}\n\n"
        "Бізнес одразу перейде на Free, власник отримає повідомлення. "
        "Дані не видаляються.",
        keyboard
    )


async def platform_disable_pro_confirmed(update, context):
    query = update.callback_query

    if not is_platform_owner(query.from_user.id):
        await query.answer()
        return

    business_id = int(query.data.rsplit("_", 1)[1])
    business = get_business_by_id(business_id)

    if not business:
        await query.answer("⚠️ Бізнес не знайдено.", show_alert=True)
        return

    disable_pro(business_id)

    await _notify_business_owner(
        context.bot,
        business,
        f"ℹ️ Тариф «{business['name']}» змінено на Free.\n\n"
        "Усі ваші дані збережені. Деталі — у «💎 Мій тариф» або /plan."
    )

    await query.answer("🆓 Переведено на Free", show_alert=True)

    text, keyboard = _business_detail_message(business_id)
    await _show_in_place(query, text, keyboard)


def get_platform_handlers():
    return [
        CallbackQueryHandler(
            platform_extend_pro, pattern=r"^platform_pro_\d+_\d+$"
        ),
        CallbackQueryHandler(
            platform_disable_pro, pattern=r"^platform_prooff_\d+$"
        ),
        CallbackQueryHandler(
            platform_disable_pro_confirmed, pattern=r"^platform_prooffyes_\d+$"
        ),
        CommandHandler("platform", platform_start),
        CallbackQueryHandler(
            platform_business_detail, pattern=r"^platform_biz_\d+$"
        ),
        CallbackQueryHandler(
            platform_back_to_list, pattern=r"^platform_back$"
        ),
        CallbackQueryHandler(
            platform_close, pattern=r"^platform_close$"
        ),
    ]
