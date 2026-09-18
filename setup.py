from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import get_connection


NAME, CATEGORY, CITY = range(3)


async def setup_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    owner_id = update.effective_user.id

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

    if business:
        await update.message.reply_text(
            f"⚠️ У вас уже є бізнес: {business['name']}"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🚀 Створимо ваш BizBot.\n\n"
        "Як називається ваш бізнес?"
    )

    return NAME


async def setup_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["setup_name"] = update.message.text.strip()

    await update.message.reply_text(
        "Чудово 👍\n\n"
        "Яка категорія вашого бізнесу?\n\n"
        "Наприклад:\n"
        "• Барбершоп\n"
        "• Салон краси\n"
        "• Детейлінг\n"
        "• Репетитор"
    )

    return CATEGORY


async def setup_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["setup_category"] = update.message.text.strip()

    await update.message.reply_text(
        "📍 У якому місті знаходиться бізнес?"
    )

    return CITY


async def setup_city(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    city = update.message.text.strip()

    owner_id = update.effective_user.id
    name = context.user_data["setup_name"]
    category = context.user_data["setup_category"]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO businesses
        (owner_telegram_id, name, category, city)
        VALUES (?, ?, ?, ?)
        """,
        (
            owner_id,
            name,
            category,
            city
        )
    )

    business_id = cursor.lastrowid

    conn.commit()
    conn.close()

    context.user_data.pop("setup_name", None)
    context.user_data.pop("setup_category", None)

    await update.message.reply_text(
        "✅ Бізнес створено!\n\n"
        f"🏢 {name}\n"
        f"🏷 {category}\n"
        f"📍 {city}\n\n"
        f"Business ID: {business_id}\n\n"
        "Наступний крок — додамо ваші послуги.\n\nНатисніть /addservice, щоб додати першу послугу."
    )

    return ConversationHandler.END


async def setup_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.pop("setup_name", None)
    context.user_data.pop("setup_category", None)

    await update.message.reply_text(
        "❌ Налаштування скасовано."
    )

    return ConversationHandler.END


def get_setup_handler():

    return ConversationHandler(
        entry_points=[
            CommandHandler("setup", setup_start)
        ],

        states={
            NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setup_name
                )
            ],

            CATEGORY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setup_category
                )
            ],

            CITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setup_city
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                setup_cancel
            )
        ],
    )