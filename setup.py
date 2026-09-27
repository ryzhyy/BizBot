from telegram import Update
from conversation_utils import interrupt_handlers
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import sqlite3

import input_limits
from database import get_connection, generate_unique_slug
from input_limits import clean_text, too_long_text
from owner_events import on_setup_started, on_business_created


NAME, CATEGORY, CITY = range(3)


def create_business(owner_id, name, category, city, attempts=3):
    """Business id, or None if this owner already has a business.

    The slug is picked before the insert, so two owners creating
    businesses with the same name at the same moment can pick the same
    slug; the loser retries with a fresh one instead of being told it
    already has a business."""
    for _ in range(attempts):
        conn = get_connection()
        try:
            slug = generate_unique_slug(conn.cursor(), name)
            cursor = conn.execute(
                """
                INSERT INTO businesses
                (owner_telegram_id, name, category, city, slug)
                VALUES (?, ?, ?, ?, ?)
                """,
                (owner_id, name, category, city, slug)
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            conn.rollback()
            owned = conn.execute(
                "SELECT 1 FROM businesses WHERE owner_telegram_id = ?",
                (owner_id,)
            ).fetchone()
            if owned:
                return None
        finally:
            conn.close()

    raise RuntimeError("Could not pick a unique slug for a new business")


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

    await on_setup_started(context, update.effective_user)

    return NAME


async def setup_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    name = clean_text(update.message.text, input_limits.BUSINESS_NAME)

    if name is None:
        await update.message.reply_text(
            too_long_text(input_limits.BUSINESS_NAME)
        )
        return NAME

    context.user_data["setup_name"] = name

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
    category = clean_text(update.message.text, input_limits.BUSINESS_CATEGORY)

    if category is None:
        await update.message.reply_text(
            too_long_text(input_limits.BUSINESS_CATEGORY)
        )
        return CATEGORY

    context.user_data["setup_category"] = category

    await update.message.reply_text(
        "📍 У якому місті знаходиться бізнес?"
    )

    return CITY


async def setup_city(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    city = clean_text(update.message.text, input_limits.BUSINESS_CITY)

    if city is None:
        await update.message.reply_text(
            too_long_text(input_limits.BUSINESS_CITY)
        )
        return CITY

    owner_id = update.effective_user.id
    name = context.user_data["setup_name"]
    category = context.user_data["setup_category"]

    business_id = create_business(owner_id, name, category, city)

    if business_id is None:
        # Two /setup flows for the same owner finished at the same
        # time; the UNIQUE(owner_telegram_id) constraint let only one
        # through, so this one has nothing to create.
        context.user_data.pop("setup_name", None)
        context.user_data.pop("setup_category", None)

        await update.message.reply_text(
            "⚠️ У вас уже є бізнес."
        )
        return ConversationHandler.END

    context.user_data.pop("setup_name", None)
    context.user_data.pop("setup_category", None)

    await on_business_created(
        context, update.effective_user, name, category, city
    )

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
    interrupts = interrupt_handlers(
        cleanup_keys=("setup_name", "setup_category"),
        action_name="Створення бізнесу"
    )

    return ConversationHandler(
        entry_points=[
            CommandHandler("setup", setup_start)
        ],

        states={
            NAME: [
                *interrupts,
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setup_name
                )
            ],

            CATEGORY: [
                *interrupts,
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    setup_category
                )
            ],

            CITY: [
                *interrupts,
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
            ),
            *interrupts,
        ],
    )