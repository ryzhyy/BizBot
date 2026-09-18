from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import get_connection


SERVICE_NAME, SERVICE_PRICE, SERVICE_DURATION = range(3)


def get_owner_business(owner_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name
        FROM businesses
        WHERE owner_telegram_id = ?
        """,
        (owner_id,)
    )

    business = cursor.fetchone()
    conn.close()

    return business


async def addservice_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_owner_business(
        update.effective_user.id
    )

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return ConversationHandler.END

    context.user_data["service_business_id"] = business["id"]

    await update.message.reply_text(
        f"🏢 {business['name']}\n\n"
        "Яку послугу хочете додати?"
    )

    return SERVICE_NAME


async def service_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["service_name"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "💰 Вкажіть ціну в гривнях.\n\n"
        "Наприклад: 600"
    )

    return SERVICE_PRICE


async def service_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text.strip()

    try:
        price = int(text)
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введіть тільки число.\n"
            "Наприклад: 600"
        )
        return SERVICE_PRICE

    if price < 0:
        await update.message.reply_text(
            "⚠️ Ціна не може бути від'ємною."
        )
        return SERVICE_PRICE

    context.user_data["service_price"] = price

    await update.message.reply_text(
        "⏱ Скільки хвилин триває послуга?\n\n"
        "Наприклад: 60"
    )

    return SERVICE_DURATION


async def service_duration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text.strip()

    try:
        duration = int(text)
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введіть кількість хвилин числом.\n"
            "Наприклад: 60"
        )
        return SERVICE_DURATION

    if duration <= 0:
        await update.message.reply_text(
            "⚠️ Тривалість повинна бути більшою за 0."
        )
        return SERVICE_DURATION

    business_id = context.user_data["service_business_id"]
    name = context.user_data["service_name"]
    price = context.user_data["service_price"]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO services
        (business_id, name, price, duration)
        VALUES (?, ?, ?, ?)
        """,
        (
            business_id,
            name,
            price,
            duration
        )
    )

    service_id = cursor.lastrowid

    conn.commit()
    conn.close()

    for key in (
        "service_business_id",
        "service_name",
        "service_price"
    ):
        context.user_data.pop(key, None)
    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Додати ще послугу",
                callback_data="setup_add_service"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Завершити налаштування",
                callback_data="setup_finish"
            )
        ]
    ]

    await update.message.reply_text(
        "✅ Послугу додано!\n\n"
        f"✂️ {name}\n"
        f"💰 {price} грн\n"
        f"⏱ {duration} хв\n\n"
        "Що робимо далі? 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return ConversationHandler.END


async def services_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    business = get_owner_business(
        update.effective_user.id
    )

    if not business:
        await update.message.reply_text(
            "⚠️ Спочатку створіть бізнес через /setup."
        )
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, price, duration
        FROM services
        WHERE business_id = ?
          AND active = 1
        ORDER BY id
        """,
        (business["id"],)
    )

    services = cursor.fetchall()
    conn.close()

    if not services:
        await update.message.reply_text(
            "📭 Послуг ще немає.\n"
            "Додайте першу через /addservice."
        )
        return

    text = f"🏢 {business['name']}\n\n📋 Послуги:\n\n"

    for service in services:
        text += (
            f"#{service['id']} — {service['name']}\n"
            f"💰 {service['price']} грн\n"
            f"⏱ {service['duration']} хв\n\n"
        )

    await update.message.reply_text(text)


async def addservice_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    for key in (
        "service_business_id",
        "service_name",
        "service_price"
    ):
        context.user_data.pop(key, None)

    await update.message.reply_text(
        "❌ Додавання послуги скасовано."
    )

    return ConversationHandler.END


def get_addservice_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "addservice",
                addservice_start
            )
        ],
        states={
            SERVICE_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    service_name
                )
            ],
            SERVICE_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    service_price
                )
            ],
            SERVICE_DURATION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    service_duration
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                addservice_cancel
            )
        ],
    )


def get_services_handler():
    return CommandHandler(
        "services",
        services_list
    )